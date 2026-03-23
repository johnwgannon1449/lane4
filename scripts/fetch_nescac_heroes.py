"""
Fetch best hero images for NESCAC schools from Wikimedia Commons + Wikipedia.

Sources (in priority order):
  1. Wikimedia Commons image search for the hero_query
  2. Images on the school's Wikipedia article, scored against anchor terms

Usage:
  python3 scripts/fetch_nescac_heroes.py
"""

import sys, os, json, time, re, urllib.parse, urllib.request, urllib.error
from pathlib import Path

ROOT_DIR  = Path(__file__).parent.parent
MANIFEST  = ROOT_DIR / 'nescac_hero_manifest.json'
OUT_FILE  = ROOT_DIR / 'data' / 'school_images.json'

UA = 'Lane4Recruit/1.0 (college swim recruiting; contact@lane4.app)'
WIKI_API  = 'https://en.wikipedia.org/w/api.php'
COMM_API  = 'https://commons.wikimedia.org/w/api.php'


# ── API helpers ────────────────────────────────────────────────────────────────

def _get(api_url, params, timeout=12):
    params['format'] = 'json'
    qs  = urllib.parse.urlencode(params)
    req = urllib.request.Request(f'{api_url}?{qs}', headers={'User-Agent': UA})
    time.sleep(0.5)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 20 * (attempt + 1)
                print(f'    [429] sleeping {wait}s …')
                time.sleep(wait)
            else:
                return {}
        except Exception as e:
            print(f'    [err] {e}')
            return {}
    return {}


def commons_search_images(query, limit=20):
    """Search Wikimedia Commons for images matching query."""
    data = _get(COMM_API, {
        'action':      'query',
        'list':        'search',
        'srsearch':    f'{query} filetype:bitmap',
        'srnamespace': 6,   # File: namespace
        'srlimit':     limit,
    })
    results = data.get('query', {}).get('search', [])
    titles  = [r['title'] for r in results]
    if not titles:
        return []
    return _resolve_image_urls(titles, api=COMM_API, width=1400)


def wiki_page_images(page_title, width=1400):
    """Get all images on a Wikipedia page."""
    data = _get(WIKI_API, {
        'action':    'query',
        'titles':    page_title,
        'generator': 'images',
        'gimlimit':  '50',
        'prop':      'imageinfo',
        'iiprop':    'url|size',
        'iiurlwidth': str(width),
    })
    pages = data.get('query', {}).get('pages', {})
    images = []
    for pid, page in pages.items():
        title = page.get('title', '')
        info  = (page.get('imageinfo') or [{}])[0]
        url   = info.get('thumburl') or info.get('url', '')
        w     = info.get('thumbwidth', 0) or info.get('width', 0)
        h     = info.get('thumbheight', 0) or info.get('height', 0)
        if url:
            images.append({'title': title, 'url': url, 'w': w, 'h': h})
    return images


def wiki_search_title(query):
    """Wikipedia text search → best matching page title."""
    data = _get(WIKI_API, {
        'action':   'query',
        'list':     'search',
        'srsearch': query,
        'srlimit':  3,
    })
    results = data.get('query', {}).get('search', [])
    return results[0]['title'] if results else None


def _resolve_image_urls(file_titles, api, width=1400):
    """Batch resolve File: titles to actual image URLs via imageinfo."""
    if not file_titles:
        return []
    chunk = file_titles[:25]
    data  = _get(api, {
        'action':     'query',
        'titles':     '|'.join(chunk),
        'prop':       'imageinfo',
        'iiprop':     'url|size',
        'iiurlwidth': str(width),
    })
    pages  = data.get('query', {}).get('pages', {})
    result = []
    for pid, page in pages.items():
        title = page.get('title', '')
        info  = (page.get('imageinfo') or [{}])[0]
        url   = info.get('thumburl') or info.get('url', '')
        w     = info.get('thumbwidth', 0) or info.get('width', 0)
        h     = info.get('thumbheight', 0) or info.get('height', 0)
        if url:
            result.append({'title': title, 'url': url, 'w': w, 'h': h})
    return result


# ── image filtering / scoring ──────────────────────────────────────────────────

BAD_FNAME = re.compile(
    r'logo|seal|flag|map|icon|coat|shield|wordmark|crest|badge|'
    r'insignia|monogram|mascot|patch|mark\.|athletics_mark|spirit|'
    r'portrait|headshot|\.gif|svg|question|default|vector|locator|'
    r'blank|thumb(?!nail)|signature|letterhead',
    re.I
)

def _is_valid(img: dict) -> bool:
    fname = img['title'].lower()
    url   = img['url'].lower()
    if BAD_FNAME.search(fname) or BAD_FNAME.search(url):
        return False
    ext = fname.rsplit('.', 1)[-1] if '.' in fname else ''
    if ext and ext not in ('jpg', 'jpeg', 'png', 'webp'):
        return False
    if ext in ('gif', 'svg'):
        return False
    # Reject tiny images
    if img.get('w', 9999) < 400 or img.get('h', 9999) < 250:
        return False
    return True


def _score(img: dict, anchor_terms: list[str], school_tokens: list[str]) -> int:
    fname = img['title'].lower()
    url   = img['url'].lower()
    combined = fname + ' ' + url

    s = 0
    # Anchor term matches (from manifest)
    for term in anchor_terms:
        if term in combined:
            s += 12
    # School name token matches
    for tok in school_tokens:
        if tok and tok in combined:
            s += 6
    # Resolution bonus
    w = img.get('w', 0) or 0
    h = img.get('h', 0) or 0
    if w >= 1200: s += 8
    elif w >= 800: s += 4
    # Landscape bonus
    if w and h and w > h:
        s += 5
    # Campus/scenic keywords
    for kw in ['campus', 'aerial', 'quad', 'hall', 'chapel', 'library',
                'tower', 'grounds', 'exterior', 'building', 'panoram',
                'view', 'autumn', 'fall', 'spring', 'winter', 'mountain']:
        if kw in combined:
            s += 4
    # Penalties
    for kw in ['portrait', 'headshot', 'game', 'student', 'classroom',
                'graduation', 'ceremony', 'stadium', 'athletic']:
        if kw in combined:
            s -= 5
    return s


def _anchor_tokens(entry: dict) -> list[str]:
    """Extract key terms from the anchor + notes fields."""
    text = (entry.get('anchor', '') + ' ' +
            entry.get('hero_query', '')).lower()
    # Remove school name words and stop words
    stop = {'college', 'university', 'the', 'and', 'of', 'at', 'a', 'in',
            'for', 'with', 'view', 'campus', 'main'}
    tokens = [t for t in re.findall(r'[a-z]+', text) if t not in stop and len(t) > 3]
    return list(dict.fromkeys(tokens))  # dedup, order-preserved


def _school_tokens(name: str) -> list[str]:
    stop = {'university', 'college', 'of', 'the', 'and', 'at', 'institute',
            'technology', 'polytechnic', 'a', 'an', 'in', 'for', 'trinity',
            'connecticut'}
    return [t.lower() for t in name.split() if t.lower() not in stop][:4]


# ── per-school fetch + select ──────────────────────────────────────────────────

def fetch_candidates(school: str, entry: dict) -> list[dict]:
    query       = entry['hero_query']
    anchor_toks = _anchor_tokens(entry)
    school_toks = _school_tokens(school)

    candidates = []

    # ── 1. Wikimedia Commons search ─────────────────────────────────────────
    print(f'  [commons] {query}')
    commons_imgs = commons_search_images(query, limit=20)
    candidates.extend(commons_imgs)

    # ── 2. Wikipedia article for the school ─────────────────────────────────
    wiki_title = wiki_search_title(school)
    if wiki_title:
        print(f'  [wiki]    {wiki_title}')
        wiki_imgs = wiki_page_images(wiki_title)
        candidates.extend(wiki_imgs)

    # ── 3. Wikipedia article for specific anchor (e.g. "Bowdoin College campus") ──
    anchor_title = wiki_search_title(f'{school} campus')
    if anchor_title and anchor_title != wiki_title:
        print(f'  [wiki+]   {anchor_title}')
        extra = wiki_page_images(anchor_title)
        candidates.extend(extra)

    return candidates, anchor_toks, school_toks


def select_best(candidates: list[dict], anchor_toks, school_toks,
                used_urls: set) -> dict | None:
    valid = [c for c in candidates if _is_valid(c) and c['url'] not in used_urls]
    if not valid:
        return None
    scored = sorted(valid,
                    key=lambda c: _score(c, anchor_toks, school_toks),
                    reverse=True)
    best = scored[0]
    sc   = _score(best, anchor_toks, school_toks)
    if sc < -5:          # nothing remotely relevant — skip
        return None
    return best


# ── main ──────────────────────────────────────────────────────────────────────

def run():
    manifest = json.loads(MANIFEST.read_text())
    OUT_FILE.parent.mkdir(exist_ok=True)

    # Load any existing data/school_images.json so we don't overwrite other schools
    existing: dict = {}
    if OUT_FILE.exists():
        existing = json.loads(OUT_FILE.read_text())

    results: dict  = {}
    skipped: list  = []
    used_urls: set = set()

    # Collect URLs already used by non-NESCAC schools in existing manifest
    for school, data in existing.items():
        if school not in manifest:
            for role in ('hero', 'student_life', 'swim'):
                u = data.get(role)
                if u:
                    used_urls.add(u)

    print(f'\n{"="*60}')
    print(f'NESCAC Hero Image Fetch — {len(manifest)} schools')
    print(f'{"="*60}\n')

    for school, entry in manifest.items():
        print(f'\n── {school} ──────────────────────────────')
        print(f'   anchor: {entry["anchor"]}')

        candidates, anchor_toks, school_toks = fetch_candidates(school, entry)
        print(f'   candidates found: {len(candidates)}')

        winner = select_best(candidates, anchor_toks, school_toks, used_urls)

        if winner:
            score = _score(winner, anchor_toks, school_toks)
            print(f'   ✓ hero ({score:+d}): {winner["url"][:90]}')
            print(f'     file: {winner["title"]}')
            results[school] = {'hero': winner['url']}
            used_urls.add(winner['url'])
        else:
            print(f'   ✗ SKIPPED — no strong match found')
            skipped.append(school)

        time.sleep(0.3)

    # Merge into existing manifest (NESCAC entries overwrite/add hero only)
    for school, data in results.items():
        if school in existing:
            existing[school]['hero'] = data['hero']
        else:
            existing[school] = data

    OUT_FILE.write_text(json.dumps(existing, indent=2))

    print(f'\n{"="*60}')
    print(f'DONE  —  {len(results)} saved  |  {len(skipped)} skipped')
    if skipped:
        print(f'  Skipped: {", ".join(skipped)}')
    print(f'Output: {OUT_FILE}')
    print(f'{"="*60}\n')

    print(json.dumps(results, indent=2))
    return results, skipped


if __name__ == '__main__':
    run()
