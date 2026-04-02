"""
Lane4 Candidate Image Harvester  (Wikipedia + Pexels)
======================================================
Primary source: Wikipedia (real campus photos, no API key needed).
Fallback: Pexels (generic supplemental shots when Wikipedia yields < 4 images).

Usage:
    python3 harvest_candidates.py [--school "School Name"] [--reset]

Optional environment variable:
    PEXELS_KEY  — Pexels API key (free at pexels.com/api, 200 req/hr)

Output:
    static/candidates_manifest.json
      { "School Name": [ { url, source, width, height, score }, ... ] }
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import argparse

CANDIDATES_PATH = os.path.join('static', 'candidates_manifest.json')
NAMES_PATH      = 'school_names.json'

PEXELS_KEY  = os.environ.get('PEXELS_KEY', '')
PEXELS_URL  = 'https://api.pexels.com/v1/search'
WIKI_API    = 'https://en.wikipedia.org/w/api.php'

MIN_WIDTH  = 400
MIN_HEIGHT = 250
MAX_CANDIDATES = 8

BAD_TOKENS = [
    'seal', 'logo', 'coat_of_arms', 'crest', 'flag_of', 'wordmark',
    'insignia', 'monogram', 'mascot', 'badge', 'shield', 'patch',
    '_mark', 'icon', 'vector', 'favicon', 'thumbnail', 'sticker',
    'emblem', 'sprite', 'button', '/buttons/', 'signature',
    'map', 'locator', 'location', 'county', 'state_', '_state',
    'seal.', 'seal_', 'coa.', 'arms.',
]

BAD_EXTS = {'.svg', '.gif', '.bmp', '.tiff', '.tif', '.pdf'}

# Stop-words stripped before key-word matching
_STOP = {'university', 'college', 'of', 'the', 'at', 'and', 'a', 'an',
         'state', 'institute', 'technology', 'school'}


def _key_words(name: str) -> list[str]:
    """Return meaningful words from a school name for title validation."""
    return [w.lower() for w in name.split() if w.lower() not in _STOP and len(w) > 1]


def _title_matches(school: str, title: str) -> bool:
    """True if the Wikipedia page title plausibly refers to this school."""
    kw = _key_words(school)
    if not kw:
        return True
    tl = title.lower()
    return all(k in tl for k in kw)


def _is_bad_url(url: str) -> bool:
    u = url.lower()
    if any(u.endswith(ext) for ext in BAD_EXTS):
        return True
    return any(t in u for t in BAD_TOKENS)


def _is_bad_filename(name: str) -> bool:
    n = name.lower()
    if any(n.endswith(ext) for ext in BAD_EXTS):
        return True
    return any(t in n for t in BAD_TOKENS)


def _wiki_get(params: dict) -> dict:
    params.setdefault('format', 'json')
    params.setdefault('formatversion', '2')
    url = WIKI_API + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Lane4Recruit/2.0 (swim recruiting advisor)',
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _wiki_direct_lookup(title_candidate: str) -> str | None:
    """Try fetching a Wikipedia page by exact title. Returns normalized title or None."""
    try:
        data = _wiki_get({
            'action': 'query',
            'titles': title_candidate,
            'redirects': 1,
        })
        pages = data.get('query', {}).get('pages', [])
        if not pages:
            return None
        page = pages[0]
        if page.get('missing') or 'disambiguation' in page.get('categories', ''):
            return None
        return page.get('title')
    except Exception:
        return None


def _wiki_opensearch(query: str, limit: int = 5) -> list[str]:
    """Return page titles via opensearch (autocomplete-style, highest precision)."""
    try:
        data = _wiki_get({
            'action': 'opensearch',
            'search': query,
            'limit': limit,
            'namespace': 0,
        })
        # opensearch returns [query, [titles], [descriptions], [urls]]
        return data[1] if isinstance(data, list) and len(data) > 1 else []
    except Exception:
        return []


def _wiki_find_page(school: str) -> str | None:
    """Return the best-matching Wikipedia page title for a school."""
    low = school.lower()
    edu_words = ('university', 'college', 'academy', 'institute', 'school')
    has_edu = any(w in low for w in edu_words)

    # 1. Direct exact-title lookup
    direct = _wiki_direct_lookup(school)
    if direct and _title_matches(school, direct):
        # Extra guard: if school has no edu suffix, make sure the page title looks
        # educational (avoids landing on military/generic pages like "Air force")
        if has_edu or any(w in direct.lower() for w in edu_words):
            return direct

    # 2. For non-edu names, try educational expansions first (highest precision)
    if not has_edu:
        expansions = [
            f'University of {school}',
            f'{school} University',
            f'{school} College',
            f'{school} Academy',
            f'United States {school} Academy',
        ]
        for exp in expansions:
            d = _wiki_direct_lookup(exp)
            if d:
                return d
            for candidate in _wiki_opensearch(exp, limit=3):
                if _title_matches(school, candidate):
                    return candidate

    # 3. Opensearch — returns titles sorted by closest match to query
    for candidate in _wiki_opensearch(school, limit=5):
        if _title_matches(school, candidate):
            if has_edu or any(w in candidate.lower() for w in edu_words):
                return candidate

    # 4. Fallback opensearch accepting any title match (for schools with edu suffix
    #    that didn't match above)
    for candidate in _wiki_opensearch(school, limit=5):
        if _title_matches(school, candidate):
            return candidate

    return None


def _wiki_main_image(title: str) -> dict | None:
    """Fetch the primary infobox image at full resolution."""
    try:
        data = _wiki_get({
            'action': 'query',
            'titles': title,
            'prop': 'pageimages',
            'piprop': 'original|name',
        })
        pages = data.get('query', {}).get('pages', [])
        if not pages:
            return None
        original = pages[0].get('original', {})
        url = original.get('source', '')
        if not url or _is_bad_url(url):
            return None
        w = original.get('width', 0)
        h = original.get('height', 0)
        if w < MIN_WIDTH or h < MIN_HEIGHT:
            return None
        return {'url': url, 'source': 'wiki_main', 'width': w, 'height': h,
                'score': round((w * h) / 1_000_000 + 2.0, 3)}
    except Exception as e:
        print(f'    wiki main image error: {e}')
        return None


def _wiki_page_image_urls(title: str, limit: int = 25) -> list:
    """Get resolved image URLs from all images listed on a Wikipedia page."""
    try:
        data = _wiki_get({
            'action': 'query',
            'titles': title,
            'prop': 'images',
            'imlimit': limit,
        })
        pages = data.get('query', {}).get('pages', [])
        if not pages:
            return []
        filenames = [
            img['title'] for img in pages[0].get('images', [])
            if not _is_bad_filename(img.get('title', ''))
        ]
        if not filenames:
            return []

        data2 = _wiki_get({
            'action': 'query',
            'titles': '|'.join(filenames[:20]),
            'prop': 'imageinfo',
            'iiprop': 'url|size|mime',
        })
        results = []
        for page in data2.get('query', {}).get('pages', []):
            for info in page.get('imageinfo', []):
                mime = info.get('mime', '')
                if mime in ('image/svg+xml', 'image/gif', 'image/bmp'):
                    continue
                url = info.get('url', '')
                if not url or _is_bad_url(url):
                    continue
                w = info.get('width', 0)
                h = info.get('height', 0)
                if w < MIN_WIDTH or h < MIN_HEIGHT:
                    continue
                results.append({
                    'url': url, 'source': 'wiki_page',
                    'width': w, 'height': h,
                    'score': round((w * h) / 1_000_000, 3),
                })
        return results
    except Exception as e:
        print(f'    wiki page images error: {e}')
        return []


def _pexels_search(school: str, query_suffix: str, per_page: int = 4) -> list:
    """Pexels search fallback."""
    if not PEXELS_KEY:
        return []
    query = f'{school} {query_suffix}'
    params = {
        'query': query, 'per_page': per_page,
        'page': 1, 'orientation': 'landscape',
    }
    url = PEXELS_URL + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'Authorization': PEXELS_KEY, 'User-Agent': 'Lane4Recruit/2.0',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        results = []
        for photo in data.get('photos', []):
            w = photo.get('width', 0)
            h = photo.get('height', 0)
            if w < MIN_WIDTH or h < MIN_HEIGHT:
                continue
            src = photo.get('src', {})
            url2 = src.get('large2x') or src.get('large') or ''
            if not url2:
                continue
            results.append({
                'url': url2, 'source': 'pexels',
                'width': w, 'height': h,
                'score': round((w * h) / 1_000_000 - 0.5, 3),
            })
        return results
    except Exception:
        return []


def fetch_candidates(school: str) -> list:
    all_candidates: list[dict] = []
    seen_urls: set[str] = set()

    def _add(item: dict | None):
        if not item:
            return
        url = item.get('url', '')
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        all_candidates.append(item)

    # 1. Wikipedia: find the correct page
    print(f'  [wiki] searching: {school}')
    page_title = _wiki_find_page(school)
    if page_title:
        print(f'  [wiki] page: {page_title}')
        _add(_wiki_main_image(page_title))

        extra = _wiki_page_image_urls(page_title, limit=25)
        extra.sort(key=lambda x: x['score'], reverse=True)
        for item in extra:
            _add(item)
            if len(all_candidates) >= 6:
                break
    else:
        print(f'  [wiki] no matching page found')

    # 2. Pexels fallback if we still have room
    if len(all_candidates) < MAX_CANDIDATES:
        need = MAX_CANDIDATES - len(all_candidates)
        for suffix in ['campus buildings exterior', 'swimming pool aquatic center']:
            if len(all_candidates) >= MAX_CANDIDATES:
                break
            pex = _pexels_search(school, suffix, per_page=need)
            for item in pex:
                _add(item)

    all_candidates.sort(key=lambda x: x['score'], reverse=True)
    return all_candidates[:MAX_CANDIDATES]


def load_manifest() -> dict:
    try:
        with open(CANDIDATES_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_manifest(manifest: dict):
    os.makedirs(os.path.dirname(CANDIDATES_PATH), exist_ok=True)
    with open(CANDIDATES_PATH, 'w') as f:
        json.dump(manifest, f, indent=2)


def load_school_names() -> list:
    with open(NAMES_PATH) as f:
        return json.load(f)


def run(target_school: str | None = None, reset: bool = False):
    manifest = {} if reset else load_manifest()
    schools  = load_school_names()

    if target_school:
        schools = [s for s in schools if s.lower() == target_school.lower()]
        if not schools:
            print(f'School not found: {target_school}')
            sys.exit(1)

    done = skipped = 0
    total = len(schools)

    for i, school in enumerate(schools, 1):
        if not reset and school in manifest and manifest[school]:
            skipped += 1
            continue

        print(f'\n[{i}/{total}] {school}')
        candidates = fetch_candidates(school)
        manifest[school] = candidates
        save_manifest(manifest)
        done += 1
        print(f'         → {len(candidates)} candidates saved')
        time.sleep(0.5)

    print(f'\nDone. Harvested: {done}  Skipped: {skipped}')
    print(f'Manifest: {CANDIDATES_PATH}  ({len(manifest)} entries)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Harvest candidate images for curation UI')
    parser.add_argument('--school', default=None, help='Process a single school')
    parser.add_argument('--reset',  action='store_true', help='Re-fetch all schools')
    args = parser.parse_args()
    run(target_school=args.school, reset=args.reset)
