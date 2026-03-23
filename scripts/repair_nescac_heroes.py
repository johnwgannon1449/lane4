"""
Repair hero images for weak NESCAC schools.

Priority order for each school:
  1. Official school .edu website (homepage / admissions / about)
  2. Wikimedia Commons — school-specific query with strict filtering
  3. Wikipedia article images — strict school-name matching only

Filters applied:
  - Reject black-and-white / historical photos (year tokens in filename)
  - Reject low-resolution images (< 600 wide)
  - Reject non-school images (no school name token in filename / alt)
  - Reject generic/junk filenames

Usage:
  python3 scripts/repair_nescac_heroes.py
"""

import sys, os, json, time, re, urllib.parse, urllib.request, urllib.error
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT_DIR  = Path(__file__).parent.parent
MANIFEST  = ROOT_DIR / 'nescac_hero_manifest.json'
OUT_FILE  = ROOT_DIR / 'data' / 'school_images.json'
PAGE_CACHE= ROOT_DIR / 'data' / 'page_cache'
PAGE_CACHE.mkdir(parents=True, exist_ok=True)

UA        = 'Mozilla/5.0 (compatible; Lane4Recruit/1.0; +https://lane4.app)'
WIKI_API  = 'https://en.wikipedia.org/w/api.php'
COMM_API  = 'https://commons.wikimedia.org/w/api.php'
TIMEOUT   = 10

# ── Schools to repair + their config ─────────────────────────────────────────
REPAIR_SCHOOLS = {
    "Amherst College": {
        "base_url":    "https://www.amherst.edu",
        "must_tokens": ["amherst"],          # at least one must appear in filename/alt/url
        "preferred":   ["johnson", "chapel", "quad", "academic"],
        "hero_query":  "Amherst College Johnson Chapel academic quad",
    },
    "Connecticut College": {
        "base_url":    "https://www.conncoll.edu",
        "must_tokens": ["conncoll", "connecticut college", "conn college"],
        "preferred":   ["arboretum", "campus", "green", "blaustein"],
        "hero_query":  "Connecticut College campus arboretum New London",
    },
    "Middlebury College": {
        "base_url":    "https://www.middlebury.edu",
        "must_tokens": ["middlebury"],
        "preferred":   ["chapel", "stone", "row", "mountain", "old"],
        "hero_query":  "Middlebury College Old Chapel Old Stone Row campus Vermont",
    },
    "Bates College": {
        "base_url":    "https://www.bates.edu",
        "must_tokens": ["bates"],
        "preferred":   ["hathorn", "quad", "campus", "hall", "autumn"],
        "hero_query":  "Bates College Hathorn Hall quad campus Lewiston Maine",
    },
    "Hamilton College": {
        "base_url":    "https://www.hamilton.edu",
        "must_tokens": ["hamilton"],
        "preferred":   ["kirkland", "campus", "hilltop", "hall", "clinton"],
        "hero_query":  "Hamilton College Kirkland Hall campus Clinton New York",
    },
    "Tufts University": {
        "base_url":    "https://www.tufts.edu",
        "must_tokens": ["tufts"],
        "preferred":   ["cannon", "library", "tisch", "hill", "campus"],
        "hero_query":  "Tufts University campus Medford Tisch Library",
    },
}

# ── Junk / reject patterns ────────────────────────────────────────────────────
JUNK = re.compile(
    r'logo|seal|flag|map|icon|coat|shield|wordmark|crest|badge|insignia|'
    r'monogram|mascot|patch|\.gif|svg|avatar|thumb(?!nail)|placeholder|'
    r'spinner|blank|social|twitter|facebook|instagram|linkedin|youtube|'
    r'portrait|headshot|locator|chart|graphic|lockup|tile[-_]|bullet|'
    r'question|default|vector|signature|letterhead',
    re.I
)

# Historical / B&W detection — reject files with these patterns
HISTORICAL = re.compile(
    r'\b(ca\.?\s*1[5-9]\d\d|1[5-9]\d\d|18\d\d|190\d|191\d|192\d|'
    r'histor|archiv|vintage|antique|sepia|black.?white|b&w|bw_)\b',
    re.I
)


def _is_historical(text: str) -> bool:
    return bool(HISTORICAL.search(text))


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def fetch_html(url: str) -> str | None:
    import hashlib
    key  = hashlib.md5(url.encode()).hexdigest()
    path = PAGE_CACHE / f'{key}.html'
    if path.exists():
        return path.read_text(errors='replace')
    try:
        r = requests.get(url, timeout=TIMEOUT,
                         headers={'User-Agent': UA}, allow_redirects=True)
        if r.status_code == 200 and 'text/html' in r.headers.get('content-type', ''):
            path.write_text(r.text, errors='replace')
            return r.text
    except Exception:
        pass
    return None


def _wiki_get(api_url: str, params: dict, timeout=14) -> dict:
    params['format'] = 'json'
    qs  = urllib.parse.urlencode(params)
    req = urllib.request.Request(f'{api_url}?{qs}', headers={'User-Agent': UA})
    time.sleep(0.6)
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
        except Exception:
            return {}
    return {}


# ── SOURCE 1: official .edu website ──────────────────────────────────────────

def _best_srcset(srcset: str) -> str:
    best_url, best_w = '', 0
    for part in srcset.split(','):
        pieces = part.strip().split()
        if not pieces:
            continue
        u = pieces[0]
        w = 0
        if len(pieces) > 1 and pieces[1].endswith('w'):
            try:
                w = int(pieces[1][:-1])
            except ValueError:
                pass
        if w > best_w:
            best_w, best_url = w, u
    return best_url or srcset.split(',')[0].strip().split()[0]


def scrape_official_site(base_url: str, school_name: str) -> list[dict]:
    """Fetch homepage + admissions + about from official site, extract images."""
    paths = ['/', '/admissions', '/about', '/about-us', '/campus-life',
             '/discover', '/explore']
    candidates = []
    fetched = 0
    for p in paths:
        if fetched >= 4:
            break
        url = base_url.rstrip('/') + p
        html = fetch_html(url)
        if not html:
            continue
        fetched += 1
        soup = BeautifulSoup(html, 'html.parser')
        seen: set = set()

        # og:image
        og = soup.find('meta', property='og:image')
        if og and og.get('content'):
            raw = og['content']
            full = urljoin(url, raw)
            if full not in seen:
                seen.add(full)
                candidates.append({
                    'url':    full,
                    'alt':    '',
                    'fname':  full.rsplit('/', 1)[-1].split('?')[0].lower(),
                    'source': 'official_og',
                    'page':   url,
                    'w':      0,
                })

        for img in soup.find_all('img'):
            raw     = img.get('data-src') or img.get('data-lazy-src') or img.get('src', '')
            srcset  = img.get('srcset') or img.get('data-srcset', '')
            alt     = img.get('alt', '')
            chosen  = ''
            if srcset:
                chosen = _best_srcset(srcset)
            elif raw:
                chosen = raw
            if not chosen:
                continue
            full  = urljoin(url, chosen).split('?')[0]
            fname = full.rsplit('/', 1)[-1].lower()
            if full in seen:
                continue
            seen.add(full)
            candidates.append({
                'url':    full,
                'alt':    alt,
                'fname':  fname,
                'source': 'official',
                'page':   url,
                'w':      0,
            })

    return candidates


# ── SOURCE 2: Wikimedia Commons ───────────────────────────────────────────────

def _resolve_file_titles(titles: list[str], api: str, width=1400) -> list[dict]:
    if not titles:
        return []
    data = _wiki_get(api, {
        'action':     'query',
        'titles':     '|'.join(titles[:25]),
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
            result.append({
                'url':    url,
                'alt':    title,
                'fname':  title.lower().replace('file:', ''),
                'source': 'wikimedia',
                'page':   'commons',
                'w':      w,
                'h':      h,
            })
    return result


def commons_search(query: str, limit=20) -> list[dict]:
    data = _wiki_get(COMM_API, {
        'action':      'query',
        'list':        'search',
        'srsearch':    f'{query} filetype:bitmap',
        'srnamespace': 6,
        'srlimit':     limit,
    })
    titles = [r['title'] for r in data.get('query', {}).get('search', [])]
    return _resolve_file_titles(titles, COMM_API)


def wiki_article_images(page_title: str, width=1400) -> list[dict]:
    data = _wiki_get(WIKI_API, {
        'action':    'query',
        'titles':    page_title,
        'generator': 'images',
        'gimlimit':  '50',
        'prop':      'imageinfo',
        'iiprop':    'url|size',
        'iiurlwidth': str(width),
    })
    pages = data.get('query', {}).get('pages', {})
    result = []
    for pid, page in pages.items():
        title = page.get('title', '')
        info  = (page.get('imageinfo') or [{}])[0]
        url   = info.get('thumburl') or info.get('url', '')
        w     = info.get('thumbwidth', 0) or info.get('width', 0)
        h     = info.get('thumbheight', 0) or info.get('height', 0)
        if url:
            result.append({
                'url':    url,
                'alt':    title,
                'fname':  title.lower().replace('file:', ''),
                'source': 'wikipedia',
                'page':   page_title,
                'w':      w,
                'h':      h,
            })
    return result


def wiki_search_title(query: str) -> str | None:
    data = _wiki_get(WIKI_API, {
        'action':   'query',
        'list':     'search',
        'srsearch': query,
        'srlimit':  1,
    })
    results = data.get('query', {}).get('search', [])
    return results[0]['title'] if results else None


# ── Filtering ─────────────────────────────────────────────────────────────────

def filter_candidate(c: dict, must_tokens: list[str], school: str,
                     used_urls: set) -> tuple[bool, str]:
    """Return (keep, reject_reason)."""
    url   = c['url']
    fname = c.get('fname', url.rsplit('/', 1)[-1].lower())
    alt   = c.get('alt', '').lower()
    combined = f'{fname} {alt} {url.lower()}'

    if url in used_urls:
        return False, 'already used by another school'

    ext = fname.rsplit('.', 1)[-1] if '.' in fname else ''
    if ext in ('svg', 'gif', 'ico'):
        return False, f'bad extension: {ext}'
    if ext and ext not in ('jpg', 'jpeg', 'png', 'webp', ''):
        return False, f'unsupported extension: {ext}'

    if JUNK.search(combined):
        return False, 'junk/logo pattern'

    if _is_historical(fname) or _is_historical(alt):
        return False, 'historical/B&W (year token in filename/alt)'

    w = c.get('w', 0) or 0
    if w and w < 500:
        return False, f'too small: {w}px wide'

    # For Wikimedia/Wikipedia sources, require school name token in filename or alt
    if c.get('source') in ('wikimedia', 'wikipedia'):
        if must_tokens and not any(tok in combined for tok in must_tokens):
            return False, f'no school name match (must_tokens={must_tokens})'

    return True, ''


def score_candidate(c: dict, preferred: list[str]) -> int:
    fname    = c.get('fname', '').lower()
    alt      = c.get('alt', '').lower()
    url      = c.get('url', '').lower()
    combined = f'{fname} {alt} {url}'

    s = 0

    # Source priority
    if c.get('source') == 'official_og':
        s += 15
    elif c.get('source') == 'official':
        s += 12
    elif c.get('source') == 'wikimedia':
        s += 4
    elif c.get('source') == 'wikipedia':
        s += 2

    # Preferred landmark/keyword match
    for kw in preferred:
        if kw in combined:
            s += 10

    # Resolution
    w = c.get('w', 0) or 0
    if w >= 1200: s += 8
    elif w >= 800: s += 5
    elif w >= 600: s += 2

    # Landscape
    h = c.get('h', 0) or 0
    if w and h and w > h:
        s += 5

    # Good campus keywords
    for kw in ['campus', 'quad', 'hall', 'chapel', 'library', 'exterior',
                'building', 'view', 'arboretum', 'aerial']:
        if kw in combined:
            s += 3

    # Penalise
    for kw in ['student', 'graduation', 'ceremony', 'game', 'event',
                'athlete', 'pool', 'team', 'portrait', 'indoor', 'classroom']:
        if kw in combined:
            s -= 4

    return s


# ── Main per-school flow ──────────────────────────────────────────────────────

def process_school(school: str, cfg: dict, used_urls: set) -> dict:
    """Returns dict with keys: chosen_url, debug."""
    base_url     = cfg['base_url']
    must_tokens  = cfg['must_tokens']
    preferred    = cfg['preferred']
    hero_query   = cfg['hero_query']

    debug = {
        'school':     school,
        'sources':    [],
        'candidates': [],
        'rejected':   [],
        'chosen':     None,
        'chosen_score': None,
        'chosen_reason': '',
    }

    all_candidates = []

    # ── Source 1: official .edu ──────────────────────────────────────────────
    print(f'  [official] scraping {base_url} …')
    official = scrape_official_site(base_url, school)
    debug['sources'].append({'type': 'official', 'count': len(official)})
    all_candidates.extend(official)

    # ── Source 2: Wikimedia Commons ──────────────────────────────────────────
    print(f'  [commons]  {hero_query!r}')
    commons = commons_search(hero_query, limit=20)
    debug['sources'].append({'type': 'wikimedia_commons', 'count': len(commons)})
    all_candidates.extend(commons)

    # ── Source 3: Wikipedia article (school name only, no wiki+) ─────────────
    wiki_title = wiki_search_title(school)
    if wiki_title:
        print(f'  [wiki]     article: {wiki_title!r}')
        wiki_imgs = wiki_article_images(wiki_title)
        debug['sources'].append({'type': 'wikipedia', 'count': len(wiki_imgs),
                                 'article': wiki_title})
        all_candidates.extend(wiki_imgs)
    else:
        print(f'  [wiki]     no article found')

    print(f'  total raw candidates: {len(all_candidates)}')

    # ── Filter ───────────────────────────────────────────────────────────────
    valid   = []
    for c in all_candidates:
        keep, reason = filter_candidate(c, must_tokens, school, used_urls)
        entry = {
            'url':    c['url'],
            'fname':  c.get('fname', ''),
            'source': c.get('source', ''),
            'w':      c.get('w', 0),
        }
        if keep:
            valid.append(c)
            debug['candidates'].append(entry)
        else:
            debug['rejected'].append({**entry, 'reason': reason})

    print(f'  valid after filtering: {len(valid)}  '
          f'(rejected {len(all_candidates)-len(valid)})')

    if not valid:
        print(f'  ✗ SKIPPED — no valid candidates')
        debug['chosen_reason'] = 'no valid candidates after filtering'
        return {'chosen_url': None, 'debug': debug}

    # ── Score + pick ─────────────────────────────────────────────────────────
    scored = sorted(valid,
                    key=lambda c: score_candidate(c, preferred),
                    reverse=True)
    winner = scored[0]
    sc     = score_candidate(winner, preferred)

    if sc < 0:
        print(f'  ✗ SKIPPED — best candidate score too low ({sc:+d})')
        debug['chosen_reason'] = f'best score {sc:+d} below threshold'
        return {'chosen_url': None, 'debug': debug}

    debug['chosen']       = winner['url']
    debug['chosen_score'] = sc
    debug['chosen_reason'] = (
        f'highest score ({sc:+d}) from source={winner.get("source")} '
        f'file={winner.get("fname","")[:60]}'
    )
    print(f'  ✓ chosen (score {sc:+d}, source={winner["source"]}):')
    print(f'    {winner["url"][:90]}')
    print(f'    fname: {winner.get("fname","")[:70]}')

    return {'chosen_url': winner['url'], 'debug': debug}


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    manifest = json.loads(MANIFEST.read_text())
    OUT_FILE.parent.mkdir(exist_ok=True)

    existing: dict = {}
    if OUT_FILE.exists():
        existing = json.loads(OUT_FILE.read_text())

    # Collect URLs already locked in by non-repair schools
    used_urls: set = set()
    for school, data in existing.items():
        if school not in REPAIR_SCHOOLS:
            for role in ('hero', 'student_life', 'swim'):
                u = data.get(role)
                if u:
                    used_urls.add(u)

    all_debug  = {}
    saved      = []
    skipped    = []

    print(f'\n{"="*62}')
    print(f'NESCAC Hero REPAIR — {len(REPAIR_SCHOOLS)} schools')
    print(f'{"="*62}\n')

    for school, cfg in REPAIR_SCHOOLS.items():
        print(f'\n── {school} {"─"*(50-len(school))}')
        print(f'   anchor: {manifest.get(school, {}).get("anchor", "")}')

        result     = process_school(school, cfg, used_urls)
        chosen_url = result['chosen_url']
        debug      = result['debug']
        all_debug[school] = debug

        if chosen_url:
            if school in existing:
                existing[school]['hero'] = chosen_url
            else:
                existing[school] = {'hero': chosen_url}
            used_urls.add(chosen_url)
            saved.append(school)
        else:
            skipped.append(school)

        time.sleep(0.3)

    OUT_FILE.write_text(json.dumps(existing, indent=2))

    print(f'\n{"="*62}')
    print(f'DONE  —  {len(saved)} saved  |  {len(skipped)} skipped')
    if skipped:
        print(f'  Skipped: {", ".join(skipped)}')

    # ── Full debug output ─────────────────────────────────────────────────────
    print(f'\n{"="*62}')
    print('FULL DEBUG')
    print(f'{"="*62}')
    for school, d in all_debug.items():
        print(f'\n▸ {school}')
        print(f'  Sources:')
        for s in d['sources']:
            print(f'    {s["type"]}: {s["count"]} raw')
        print(f'  Valid candidates ({len(d["candidates"])}):')
        for c in d['candidates'][:8]:
            print(f'    [{c["source"]:12s} w={c["w"] or "?":>5}] {c["url"][:80]}')
        if len(d['candidates']) > 8:
            print(f'    … +{len(d["candidates"])-8} more')
        print(f'  Rejected ({len(d["rejected"])}):')
        for r in d['rejected'][:6]:
            print(f'    [{r["source"]:12s}] {r["fname"][:40]:40s}  → {r["reason"]}')
        if len(d['rejected']) > 6:
            print(f'    … +{len(d["rejected"])-6} more')
        print(f'  → Chosen: {d["chosen"] or "SKIPPED"}')
        print(f'    Reason:  {d["chosen_reason"]}')

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f'\n{"="*62}')
    print('FINAL school_images.json (NESCAC hero entries)')
    print(f'{"="*62}')
    nescac_out = {k: v for k, v in existing.items() if k in manifest}
    print(json.dumps(nescac_out, indent=2))

    return saved, skipped


if __name__ == '__main__':
    run()
