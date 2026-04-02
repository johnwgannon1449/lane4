"""
Lane4 Candidate Image Harvester  (Official Website Scraper)
============================================================
1. Finds the school's official .edu domain via Wikipedia extlinks
2. Crawls high-value official pages (visit, campus, athletics, swimming)
3. Ranks images by page type + image characteristics
4. Falls back to Wikipedia campus photos when website scraping yields too little

Usage:
    python3 harvest_candidates.py [--school "School Name"] [--reset]

Optional env:
    PEXELS_KEY — used only as a last-resort supplement

Output:
    static/candidates_manifest.json
    static/school_domains.json   (domain cache — auto-built)
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

CANDIDATES_PATH = os.path.join('static', 'candidates_manifest.json')
DOMAINS_PATH    = os.path.join('static', 'school_domains.json')
NAMES_PATH      = 'school_names.json'

PEXELS_KEY = os.environ.get('PEXELS_KEY', '')
WIKI_API   = 'https://en.wikipedia.org/w/api.php'
PEXELS_URL = 'https://api.pexels.com/v1/search'

MIN_WIDTH  = 400
MIN_HEIGHT = 220
MAX_CANDIDATES = 12
CRAWL_WORKERS  = 6
PAGE_TIMEOUT   = 8

# ── Page paths to try, in priority order ─────────────────────────────────────
# (path, page_type)   page_type → base score multiplier
PRIORITY_PATHS = [
    ('/athletics/swimming-diving',        'swim'),
    ('/athletics/swimming',               'swim'),
    ('/sports/swimming-diving',           'swim'),
    ('/sports/swimming',                  'swim'),
    ('/aquatics',                         'swim'),
    ('/swimming',                         'swim'),
    ('/swim',                             'swim'),
    ('/facilities/natatorium',            'swim'),
    ('/visit',                            'visit'),
    ('/admissions/visit',                 'visit'),
    ('/visit-campus',                     'visit'),
    ('/about/visit',                      'visit'),
    ('/campus-life',                      'campus'),
    ('/campus-life/',                     'campus'),
    ('/student-life',                     'campus'),
    ('/about/campus',                     'campus'),
    ('/traditions',                       'campus'),
    ('/campus',                           'campus'),
    ('/about',                            'campus'),
    ('/facilities',                       'campus'),
    ('/athletics',                        'athletics'),
    ('/',                                 'home'),
]

PAGE_TYPE_SCORE = {
    'swim':      4.0,
    'visit':     3.0,
    'campus':    2.5,
    'athletics': 2.0,
    'home':      1.5,
    'general':   1.0,
}

BAD_EXTS   = {'.svg', '.gif', '.bmp', '.tiff', '.tif', '.pdf', '.ico', '.webp'}
BAD_TOKENS = [
    'seal', 'logo', 'crest', 'flag_of', 'wordmark', 'insignia', 'monogram',
    'mascot', 'badge', 'shield', 'patch', '_mark', 'icon_', 'favicon',
    'thumbnail', 'sticker', 'emblem', 'sprite', '/button', 'signature',
    'map', 'locator', 'county_', 'state_map', 'seal.', 'coa.', 'arms.',
    'placeholder', 'spacer', 'pixel.', '1x1', 'blank.', 'transparent.',
    'avatar', 'gravatar', 'generic-', 'default-user', 'default-photo',
]

BOOST_IMG_TOKENS = [
    'campus', 'facility', 'pool', 'aerial', 'natatorium', 'quad',
    'exterior', 'architecture', 'aquatic', 'swim', 'athletic',
    'field', 'stadium', 'arena', 'building', 'hall', 'center',
    'view', 'grounds', 'courtyard', 'clocktower', 'chapel',
]

PENALTY_IMG_TOKENS = [
    'headshot', 'portrait', 'staff_', 'faculty_', '_profile',
    'graduation', '-grad-', 'diploma', 'award', 'handshake',
    'classroom', 'lecture', 'lab-student', 'laptop', 'smiling',
    'group-photo', 'team-photo', 'promo_', 'marketing_',
]

BOOST_PAGE_TOKENS   = ['visit', 'campus', 'about', 'traditions', 'landmarks',
                        'student-life', 'facilities', 'aquatics', 'natatorium',
                        'swimming', 'athletics', 'swim', 'aquatic']
PENALTY_PAGE_TOKENS = ['support', 'advising', '/diversity', '/profile', '/apply',
                        'campaign', '/giving', '/news/', '/faculty', '/staff',
                        'fundrais', 'alumni/giving', 'accessibility']


# ── SSL context (ignore cert errors for some school sites) ────────────────────
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE


def _is_bad_url(url: str) -> bool:
    u = url.lower()
    if any(u.endswith(ext) for ext in BAD_EXTS):
        return True
    return any(t in u for t in BAD_TOKENS)


def _make_req(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36'),
        'Accept':     'text/html,application/xhtml+xml,*/*;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
    })


def _http_get_text(url: str, timeout: int = PAGE_TIMEOUT) -> str | None:
    try:
        req = _make_req(url)
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            if r.status != 200:
                return None
            raw = r.read()
            enc = r.headers.get_content_charset('utf-8') or 'utf-8'
        # Handle gzip
        try:
            import gzip as _gz
            raw = _gz.decompress(raw)
        except Exception:
            pass
        return raw.decode(enc, errors='replace')
    except Exception:
        return None


# ── HTML img extraction ───────────────────────────────────────────────────────

class _ImgParser(HTMLParser):
    """Extracts img src, width, height, alt, and srcset from an HTML page."""
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.imgs: list[dict] = []
        self._skip = False
        self._skip_tags = {'script', 'style', 'noscript', 'nav', 'footer', 'header'}
        self._depth: dict[str, int] = {}

    def handle_starttag(self, tag: str, attrs):
        if tag in self._skip_tags:
            self._depth[tag] = self._depth.get(tag, 0) + 1
            self._skip = True
            return
        if self._skip:
            return
        if tag not in ('img', 'source'):
            return
        a = dict(attrs)
        # Try multiple lazy-load src attributes
        raw = (a.get('src') or a.get('data-src') or a.get('data-lazy-src')
               or a.get('data-original') or a.get('data-lazy') or '')
        srcset = (a.get('srcset') or a.get('data-srcset') or '')

        # Prefer srcset (pick largest)
        best = _best_srcset(srcset, self.base_url)
        if best:
            raw = best['url']
            if not a.get('width') and best.get('w'):
                a['width'] = str(best['w'])

        if not raw or raw.startswith('data:'):
            return
        url = urllib.parse.urljoin(self.base_url, raw)
        try:
            w = int(a.get('width', 0))
            h = int(a.get('height', 0))
        except (ValueError, TypeError):
            w = h = 0
        self.imgs.append({
            'url': url,
            'alt': a.get('alt', ''),
            'width':  w,
            'height': h,
        })

    def handle_endtag(self, tag: str):
        if tag in self._skip_tags:
            self._depth[tag] = max(0, self._depth.get(tag, 0) - 1)
            if all(v == 0 for v in self._depth.values()):
                self._skip = False


def _best_srcset(srcset: str, base_url: str) -> dict | None:
    if not srcset:
        return None
    best_url, best_w = '', 0
    for part in srcset.split(','):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        if len(pieces) >= 2:
            u = pieces[0]
            desc = pieces[1]
            if desc.endswith('w'):
                try:
                    w = int(desc[:-1])
                    if w > best_w:
                        best_w, best_url = w, u
                except ValueError:
                    pass
        elif len(pieces) == 1:
            best_url = best_url or pieces[0]
    if best_url:
        return {'url': urllib.parse.urljoin(base_url, best_url), 'w': best_w or 0}
    return None


# ── Image scoring ─────────────────────────────────────────────────────────────

def _score_image(img: dict) -> float:
    url      = img.get('url', '')
    alt      = (img.get('alt') or '').lower()
    page_type = img.get('page_type', 'general')
    page_url  = (img.get('page_url') or '').lower()
    w = img.get('width', 0)
    h = img.get('height', 0)

    # Hard rejects
    if _is_bad_url(url):
        return 0.0
    if w > 0 and h > 0:
        if w < MIN_WIDTH or h < MIN_HEIGHT:
            return 0.0
        # Strong portrait orientation = likely headshot
        if h > w * 1.15:
            return 0.0

    score = PAGE_TYPE_SCORE.get(page_type, 1.0)

    # Resolution bonus (capped)
    if w > 0 and h > 0:
        score += min((w * h) / 1_500_000, 2.5)

    # Aspect ratio bonus
    if w > 0 and h > 0:
        ratio = w / h
        if ratio >= 1.7:
            score += 1.2
        elif ratio >= 1.3:
            score += 0.6

    # Image URL / alt text boosts
    url_low = url.lower().replace('/', ' ').replace('-', ' ').replace('_', ' ')
    combined = url_low + ' ' + alt
    for tok in BOOST_IMG_TOKENS:
        if tok in combined:
            score += 0.4
    for tok in PENALTY_IMG_TOKENS:
        if tok in combined:
            score -= 1.0

    # Page URL boosts/penalties
    for tok in BOOST_PAGE_TOKENS:
        if tok in page_url:
            score += 0.3
    for tok in PENALTY_PAGE_TOKENS:
        if tok in page_url:
            score -= 0.6

    return max(score, 0.0)


# ── Website domain lookup (via Wikipedia extlinks, cached) ────────────────────

def _load_domains() -> dict:
    try:
        with open(DOMAINS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_domains(d: dict):
    os.makedirs('static', exist_ok=True)
    with open(DOMAINS_PATH, 'w') as f:
        json.dump(d, f, indent=2)


def _wiki_get(params: dict) -> dict:
    params.setdefault('format', 'json')
    params.setdefault('formatversion', '2')
    url = WIKI_API + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Lane4Recruit/3.0 (swim recruiting; open source)',
    })
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read())


_STOP = {'university', 'college', 'of', 'the', 'at', 'and', 'a', 'an',
         'state', 'institute', 'technology', 'school'}


def _key_words(name: str) -> list[str]:
    return [w.lower() for w in name.split() if w.lower() not in _STOP and len(w) > 1]


def _title_matches(school: str, title: str) -> bool:
    kw = _key_words(school)
    if not kw:
        return True
    tl = title.lower()
    return all(k in tl for k in kw)


def _wiki_direct(title: str) -> str | None:
    try:
        data = _wiki_get({'action': 'query', 'titles': title, 'redirects': 1})
        pages = data.get('query', {}).get('pages', [])
        if pages and not pages[0].get('missing'):
            return pages[0].get('title')
    except Exception:
        pass
    return None


def _wiki_opensearch(query: str, limit: int = 5) -> list[str]:
    try:
        data = _wiki_get({'action': 'opensearch', 'search': query,
                          'limit': limit, 'namespace': 0})
        return data[1] if isinstance(data, list) and len(data) > 1 else []
    except Exception:
        return []


def _wiki_find_page(school: str) -> str | None:
    low = school.lower()
    edu_words = ('university', 'college', 'academy', 'institute', 'school')
    has_edu = any(w in low for w in edu_words)

    direct = _wiki_direct(school)
    if direct and _title_matches(school, direct):
        if has_edu or any(w in direct.lower() for w in edu_words):
            return direct

    if not has_edu:
        for exp in [f'University of {school}', f'{school} University',
                    f'{school} College', f'{school} Academy',
                    f'United States {school} Academy']:
            d = _wiki_direct(exp)
            if d:
                return d
            for c in _wiki_opensearch(exp, limit=3):
                if _title_matches(school, c):
                    return c

    for c in _wiki_opensearch(school, limit=5):
        if _title_matches(school, c):
            if has_edu or any(w in c.lower() for w in edu_words):
                return c

    for c in _wiki_opensearch(school, limit=5):
        if _title_matches(school, c):
            return c

    return None


def _find_school_domain(wiki_title: str) -> str | None:
    """Return https://school.edu base URL via Wikipedia extlinks."""
    try:
        data = _wiki_get({'action': 'query', 'titles': wiki_title,
                          'prop': 'extlinks', 'ellimit': 50})
        pages = data.get('query', {}).get('pages', [])
        if not pages:
            return None

        # Group all .edu hosts by their 2-part base (e.g. 'american.edu')
        base_count: dict[str, int] = {}
        base_hosts: dict[str, list[str]] = {}
        for link in pages[0].get('extlinks', []):
            url = link.get('url', link.get('*', ''))
            if not url or 'wikipedia' in url:
                continue
            parsed = urllib.parse.urlparse(url)
            host = parsed.netloc.lower()
            if not host.endswith('.edu'):
                continue
            parts = host.split('.')
            if len(parts) < 2:
                continue
            base = '.'.join(parts[-2:])          # e.g. 'american.edu'
            base_count[base] = base_count.get(base, 0) + 1
            base_hosts.setdefault(base, []).append(host)

        if not base_count:
            return None

        # Pick the base domain that appears most often (most likely the main site)
        best_base = max(base_count, key=lambda b: base_count[b])

        # Probe whether www. or bare domain is reachable — try www first
        for candidate in [f'https://www.{best_base}', f'https://{best_base}']:
            try:
                req = urllib.request.Request(candidate + '/', headers={
                    'User-Agent': 'Lane4Recruit/3.0'})
                with urllib.request.urlopen(req, timeout=6, context=_SSL_CTX) as r:
                    if r.status < 400:
                        # Return the final URL after redirects (could be http→https)
                        final = r.url.rstrip('/')
                        parsed = urllib.parse.urlparse(final)
                        return f'{parsed.scheme}://{parsed.netloc}'
            except Exception:
                pass

        # If probing fails just return www. form anyway
        return f'https://www.{best_base}'

    except Exception as e:
        print(f'    domain lookup error: {e}')
        return None


def get_school_domain(school: str, domains_cache: dict) -> str | None:
    if school in domains_cache:
        return domains_cache[school] or None
    wiki_title = _wiki_find_page(school)
    if not wiki_title:
        domains_cache[school] = ''
        return None
    domain = _find_school_domain(wiki_title)
    domains_cache[school] = domain or ''
    return domain


# ── Official website crawling ─────────────────────────────────────────────────

def _crawl_page(page_url: str, page_type: str) -> list[dict]:
    """Fetch one page, extract and score all candidate images."""
    html = _http_get_text(page_url)
    if not html:
        return []
    parser = _ImgParser(page_url)
    try:
        parser.feed(html)
    except Exception:
        pass

    seen: set[str] = set()
    results = []
    for img in parser.imgs:
        url = img['url']
        if not url or url in seen:
            continue
        seen.add(url)
        img['page_type'] = page_type
        img['page_url']  = page_url
        img['source']    = f'web_{page_type}'
        s = _score_image(img)
        if s <= 0:
            continue
        img['score'] = round(s, 3)
        results.append(img)
    return results


def _fetch_from_website(domain: str) -> list[dict]:
    """Crawl priority pages from the school's official website in parallel."""
    to_crawl = []
    seen_paths: set[str] = set()
    for path, page_type in PRIORITY_PATHS:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        to_crawl.append((domain.rstrip('/') + path, page_type))

    all_imgs: list[dict] = []
    seen_urls: set[str] = set()

    with ThreadPoolExecutor(max_workers=CRAWL_WORKERS) as ex:
        futures = {ex.submit(_crawl_page, url, pt): (url, pt) for url, pt in to_crawl}
        for future in as_completed(futures):
            try:
                imgs = future.result()
                for img in imgs:
                    u = img['url']
                    if u not in seen_urls:
                        seen_urls.add(u)
                        all_imgs.append(img)
            except Exception:
                pass

    return all_imgs


# ── Wikipedia fallback ────────────────────────────────────────────────────────

def _wiki_main_image(title: str) -> dict | None:
    try:
        data = _wiki_get({'action': 'query', 'titles': title,
                          'prop': 'pageimages', 'piprop': 'original|name'})
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
        if h > 0 and w / h < 1.0:
            return None  # portrait
        return {'url': url, 'source': 'wiki_main', 'width': w, 'height': h,
                'score': round((w * h) / 1_000_000 + 1.5, 3),
                'page_type': 'campus', 'page_url': ''}
    except Exception:
        return None


def _wiki_page_images(title: str, limit: int = 25) -> list[dict]:
    BAD_FILENAMES = BAD_TOKENS + ['.svg', '.gif', '.bmp', 'portrait', 'headshot']
    try:
        data = _wiki_get({'action': 'query', 'titles': title,
                          'prop': 'images', 'imlimit': limit})
        pages = data.get('query', {}).get('pages', [])
        if not pages:
            return []
        filenames = [img['title'] for img in pages[0].get('images', [])
                     if not any(t in img.get('title', '').lower() for t in BAD_FILENAMES)]
        if not filenames:
            return []
        data2 = _wiki_get({'action': 'query', 'titles': '|'.join(filenames[:20]),
                           'prop': 'imageinfo', 'iiprop': 'url|size|mime'})
        results = []
        for page in data2.get('query', {}).get('pages', []):
            for info in page.get('imageinfo', []):
                if info.get('mime') in ('image/svg+xml', 'image/gif', 'image/bmp'):
                    continue
                url = info.get('url', '')
                if not url or _is_bad_url(url):
                    continue
                w = info.get('width', 0)
                h = info.get('height', 0)
                if w < MIN_WIDTH or h < MIN_HEIGHT:
                    continue
                if h > 0 and w / h < 1.0:
                    continue
                results.append({'url': url, 'source': 'wiki_page',
                                'width': w, 'height': h,
                                'score': round((w * h) / 1_000_000, 3),
                                'page_type': 'campus', 'page_url': ''})
        return results
    except Exception:
        return []


def _wiki_candidates(school: str) -> list[dict]:
    wiki_title = _wiki_find_page(school)
    if not wiki_title:
        return []
    print(f'    [wiki] page: {wiki_title}')
    results = []
    main = _wiki_main_image(wiki_title)
    if main:
        results.append(main)
    results.extend(_wiki_page_images(wiki_title))
    return results


# ── Pexels last resort ────────────────────────────────────────────────────────

def _pexels_search(query: str, n: int = 4) -> list[dict]:
    if not PEXELS_KEY or n <= 0:
        return []
    params = {'query': query, 'per_page': n, 'page': 1, 'orientation': 'landscape'}
    url = PEXELS_URL + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'Authorization': PEXELS_KEY,
                                               'User-Agent': 'Lane4Recruit/3.0'})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
        results = []
        for photo in data.get('photos', []):
            w = photo.get('width', 0)
            h = photo.get('height', 0)
            if w < MIN_WIDTH or h < MIN_HEIGHT:
                continue
            src = photo.get('src', {})
            u = src.get('large2x') or src.get('large') or ''
            if not u:
                continue
            results.append({'url': u, 'source': 'pexels', 'width': w, 'height': h,
                            'score': round((w * h) / 2_000_000, 3),
                            'page_type': 'general', 'page_url': ''})
        return results
    except Exception:
        return []


# ── Main entry point ──────────────────────────────────────────────────────────

def fetch_candidates(school: str, domains_cache: dict | None = None) -> list[dict]:
    if domains_cache is None:
        domains_cache = _load_domains()

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

    # ── 1. Official website scraping ─────────────────────────────────────────
    domain = get_school_domain(school, domains_cache)
    _save_domains(domains_cache)     # persist after each lookup

    if domain:
        print(f'    [web] domain: {domain}')
        web_imgs = _fetch_from_website(domain)
        web_imgs.sort(key=lambda x: x.get('score', 0), reverse=True)
        for img in web_imgs:
            _add(img)
    else:
        print(f'    [web] no domain found — using Wikipedia')

    # ── 2. Wikipedia supplement / fallback ───────────────────────────────────
    if len(all_candidates) < 6:
        wiki_imgs = _wiki_candidates(school)
        wiki_imgs.sort(key=lambda x: x.get('score', 0), reverse=True)
        for img in wiki_imgs:
            _add(img)

    # ── 3. Pexels last resort ────────────────────────────────────────────────
    if len(all_candidates) < 4:
        for suffix in [f'{school} campus', f'{school} swimming pool']:
            if len(all_candidates) >= MAX_CANDIDATES:
                break
            for img in _pexels_search(suffix, n=3):
                _add(img)

    all_candidates.sort(key=lambda x: x.get('score', 0), reverse=True)
    return all_candidates[:MAX_CANDIDATES]


# ── Manifest helpers ──────────────────────────────────────────────────────────

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
    domains  = _load_domains()
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
        candidates = fetch_candidates(school, domains_cache=domains)
        manifest[school] = candidates
        save_manifest(manifest)
        done += 1
        print(f'    → {len(candidates)} candidates saved')
        time.sleep(0.3)

    print(f'\nDone. Harvested: {done}  Skipped: {skipped}')
    print(f'Manifest: {CANDIDATES_PATH}  ({len(manifest)} entries)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Harvest candidate images for curation UI')
    parser.add_argument('--school', default=None, help='Process a single school')
    parser.add_argument('--reset',  action='store_true', help='Re-fetch all schools')
    args = parser.parse_args()
    run(target_school=args.school, reset=args.reset)
