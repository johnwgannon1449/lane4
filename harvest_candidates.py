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

CANDIDATES_PATH = os.path.join('static', 'data', 'candidates_manifest.json')
DOMAINS_PATH    = os.path.join('static', 'data', 'school_domains.json')
NAMES_PATH      = 'school_names.json'

PEXELS_KEY      = os.environ.get('PEXELS_KEY', '')
GOOGLE_CSE_KEY  = os.environ.get('GOOGLE_CSE_KEY', '')
GOOGLE_CSE_ID   = os.environ.get('GOOGLE_CSE_ID', '')
WIKI_API        = 'https://en.wikipedia.org/w/api.php'
COMMONS_API     = 'https://commons.wikimedia.org/w/api.php'
PEXELS_URL      = 'https://api.pexels.com/v1/search'
GOOGLE_CSE_URL  = 'https://www.googleapis.com/customsearch/v1'

MIN_WIDTH  = 600   # reject images narrower than 600px (low-resolution filter)
MIN_HEIGHT = 280
MAX_CANDIDATES = 80   # up to ~24 per category × 3 categories
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
    ('/natatorium',                       'swim'),
    ('/visit',                            'visit'),
    ('/admissions/visit',                 'visit'),
    ('/visit-campus',                     'visit'),
    ('/about/visit',                      'visit'),
    ('/admissions',                       'visit'),
    ('/campus-life',                      'campus'),
    ('/campus-life/',                     'campus'),
    ('/student-life',                     'campus'),
    ('/about/campus',                     'campus'),
    ('/traditions',                       'campus'),
    ('/campus',                           'campus'),
    ('/about',                            'campus'),
    ('/facilities',                       'campus'),
    ('/residential-life',                 'campus'),
    ('/res-life',                         'campus'),
    ('/housing',                          'campus'),
    ('/photos',                           'campus'),
    ('/gallery',                          'campus'),
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
    # Rankings / badge graphics
    'ranking', 'best-college', 'best_college', 'ranked-', '_ranked',
    'award-badge', 'award_badge', 'us-news', 'usnews', 'niche-badge',
    'college-ranking', 'top-college', 'top_college', '#1-', 'no1-',
    'best-value', 'best_value', 'school-badge', 'college-badge',
    # Screenshots / composites / illustrations
    'screenshot', 'collage', 'composite', 'illustration',
    'infographic', 'graphic_', '_graphic', 'promo-graphic',
    # Architectural renderings (not real photos)
    'rendering', 'architectural_rendering', 'architectural-rendering',
    '_rendering.', '-rendering.', 'render_', 'concept_render',
    # Maps / satellite imagery
    'satellite_map', 'satellite-map', 'satellite_view', 'satellite-view',
    'google_maps', 'googlemaps', 'streetview', 'street-view',
    # Document / scan artifacts
    'document_scan', 'document-scan', 'docuscan', 'scan_', '_scan.',
    # Watermarks
    'watermark_', '_watermark', 'watermarked',
    # Stock photo watermark domains
    'getty', 'shutterstock', 'alamy', 'dreamstime', 'istockphoto',
    # Social / marketing overlays
    'instagram-', 'facebook-', 'social-post', 'twitter-', 'ad-', '-ad.',
    # Very small thumbnails
    '32x32', '64x64', '48x48', '16x16',
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

# Archival / historical — heavy penalty for hero slot
HISTORICAL_TOKENS = [
    '_1800', '_1810', '_1820', '_1830', '_1840', '_1850', '_1860', '_1870', '_1880', '_1890',
    '_1900', '_1910', '_1920', '_1930', '_1940', '_1950',
    '1800s', '1900s', '1910s', '1920s', '1930s', '1940s', '1950s',
    'vintage', 'historic_photo', 'historical_photo', 'archive_photo',
    'black_and_white', '_bw_photo', 'sepia_',
]
# Regex for bare 4-digit years in filenames (1800–1959 only)
_HIST_YEAR_RE = re.compile(r'(?<![0-9])(1[89]\d{2}|1[0-5]\d{2})(?![0-9])')

# ── Category signals ──────────────────────────────────────────────────────────
_POOL_URL_TOKENS    = {'swim', 'pool', 'aquatic', 'natator', 'diving', 'aquatics'}
_STUDENT_URL_TOKENS = {'student-life', 'student_life', 'studentlife', 'students_at',
                       'campus-life', 'campus_life', 'campuslife'}
# Additional pool-related filename tokens used to filter campus Commons results
_POOL_FNAME_FILTER  = _POOL_URL_TOKENS | {
    'recreation', 'natatorium', 'fitness', 'reccenter', 'rec_center',
    'aquaticcenter', 'crec', 'recsport', 'rec_sport', 'wellness',
}

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
    """Extracts img src, width, height, alt, srcset, and og/twitter meta images."""
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.imgs: list[dict] = []
        self.og_images: list[str] = []   # og:image / twitter:image URLs
        self._skip = False
        self._skip_tags = {'script', 'style', 'noscript', 'nav', 'footer', 'header'}
        self._depth: dict[str, int] = {}
        self._img_position = 0           # counts <img> tags seen (for prominence)

    def handle_starttag(self, tag: str, attrs):
        if tag in self._skip_tags:
            self._depth[tag] = self._depth.get(tag, 0) + 1
            self._skip = True
            return

        # Capture og:image / twitter:image from <meta> tags (always in <head>)
        if tag == 'meta':
            a = dict(attrs)
            prop    = a.get('property', '').lower()
            name    = a.get('name', '').lower()
            content = a.get('content', '')
            if (prop in ('og:image', 'og:image:secure_url') or
                    name in ('twitter:image', 'twitter:image:src')):
                if content and not content.startswith('data:'):
                    url = urllib.parse.urljoin(self.base_url, content)
                    if url not in self.og_images:
                        self.og_images.append(url)
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
        self._img_position += 1
        # Images appearing in the first 4 positions get a prominence bonus
        prominence = max(0, 5 - self._img_position) if self._img_position <= 4 else 0
        self.imgs.append({
            'url':        url,
            'alt':        a.get('alt', ''),
            'width':      w,
            'height':     h,
            'prominence': prominence,
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

    # Prominence bonus: og:image (prominence=10) or early hero image (prominence 1-4)
    prominence = img.get('prominence', 0)
    if prominence > 0:
        score += min(prominence * 0.5, 4.0)

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

    # Historical / archival penalty — strongly deprioritise for hero slot
    fname_low = urllib.parse.unquote(url).lower()
    if any(t in fname_low for t in HISTORICAL_TOKENS):
        score -= 4.0
    elif _HIST_YEAR_RE.search(fname_low):
        score -= 4.0

    # Page URL boosts/penalties
    for tok in BOOST_PAGE_TOKENS:
        if tok in page_url:
            score += 0.3
    for tok in PENALTY_PAGE_TOKENS:
        if tok in page_url:
            score -= 0.6

    return max(score, 0.0)


def _pool_confidence(img: dict, school: str) -> float:
    """Return 0.0–1.0 pool confidence: how likely this image is actually the school's pool.

    Signals:
    - page_type == 'swim': strong positive (harvested from swim-specific page)
    - url/page_url/alt contains pool tokens: positive
    - url/page_url contains school key words: positive
    - source domain matches school domain (site: search): very strong positive
    - image came from wrong-school search context: negative
    """
    page_type    = img.get('page_type', 'general')
    url          = urllib.parse.unquote(img.get('url', '')).lower()
    page_url     = urllib.parse.unquote(img.get('page_url', '')).lower()
    alt          = (img.get('alt') or '').lower()
    search_ctx   = img.get('search_context', '').lower()

    score = 0.0

    # page_type == swim means it was explicitly fetched from a swim/pool page
    if page_type == 'swim':
        score += 0.5

    # Pool tokens in URL or alt
    pool_tokens = {'natator', 'aquatic', 'swim', 'pool', 'diving', 'aquatics'}
    if any(t in url for t in pool_tokens):
        score += 0.2
    if any(t in alt for t in pool_tokens):
        score += 0.15
    if any(t in page_url for t in pool_tokens):
        score += 0.15

    # School name in URL / page / alt / search context
    kw = _key_words(_search_name(school))
    combined = url + ' ' + page_url + ' ' + alt + ' ' + search_ctx
    matches = sum(1 for k in kw if k in combined)
    if kw:
        score += 0.4 * (matches / len(kw))

    # Site-restricted source = image is from school's own domain (very strong)
    if 'site:' in search_ctx:
        score += 0.25

    return min(score, 1.0)


def _pool_attribution_label(img: dict) -> str:
    """Build a short human-readable attribution line for pool images shown in admin.

    Format (first non-empty wins):
      "natatorium name" from page context
      → Source: domain.edu  |  alt text snippet
    """
    page_url = img.get('page_url', '') or ''
    alt      = (img.get('alt') or '').strip()
    ctx      = img.get('search_context', '') or ''
    source   = img.get('source', '') or ''

    parts = []

    # Domain label
    if page_url:
        try:
            host = urllib.parse.urlparse(page_url).netloc.lower().replace('www.', '')
            if host:
                parts.append(host)
        except Exception:
            pass
    elif 'wiki_commons' in source:
        parts.append('Wikimedia Commons')
    elif 'google_cse' in source:
        parts.append('Google Images')

    # Natatorium / aquatic center name from search context or alt
    _natnames = re.findall(
        r'(?i)([\w\s]+(natatorium|aquatic\s+center|aquatic\s+centre|'
        r'swim\s+complex|pool\s+complex|pool\s+facility)[\w\s]*)',
        alt + ' ' + ctx
    )
    if _natnames:
        nat = _natnames[0][0].strip()[:60]
        if nat not in parts:
            parts.append(nat)
    elif alt and len(alt) < 80:
        parts.append(alt)

    return ' · '.join(p for p in parts if p) or ''


def _assign_category(img: dict) -> str:
    """Derive display category from page_type, URL/alt/title signals, and search context."""
    page_type    = img.get('page_type', 'general')
    url_low      = urllib.parse.unquote(img.get('url', '')).lower()
    page_url_low = urllib.parse.unquote(img.get('page_url', '')).lower()
    search_ctx   = img.get('search_context', '').lower()
    # Normalize alt text and any title/caption fields
    alt_low      = (img.get('alt') or img.get('title') or img.get('caption') or '').lower()

    # page_type='swim' is set explicitly by pool-targeted harvest functions.
    # Also check filename, page URL, alt/title text, and the originating search query —
    # this catches aquatic facility images whose filenames don't contain pool words.
    _ctx_pool_tokens = {'swim', 'pool', 'aquatic', 'natator', 'diving'}
    if (page_type == 'swim'
            or any(t in url_low      for t in _POOL_URL_TOKENS)
            or any(t in page_url_low for t in _POOL_URL_TOKENS)
            or any(t in alt_low      for t in _ctx_pool_tokens)
            or any(t in search_ctx   for t in _ctx_pool_tokens)):
        return 'pool'

    # page_type='student_life' is set by student-life-targeted harvest functions.
    _ctx_student_tokens = {'student', 'campus life', 'campus_life'}
    if (page_type == 'student_life'
            or any(t in url_low      for t in _STUDENT_URL_TOKENS)
            or any(t in page_url_low for t in _STUDENT_URL_TOKENS)
            or any(t in alt_low      for t in _STUDENT_URL_TOKENS)
            or any(t in search_ctx   for t in _ctx_student_tokens)):
        return 'student_life'

    return 'campus'


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
    # Strip punctuation from each token so 'carolina,' becomes 'carolina'
    cleaned = [w.strip('.,;:\'"()[]') for w in name.split()]
    return [w.lower() for w in cleaned if w.lower() not in _STOP and len(w) > 1]


_INVERSION_RE = re.compile(
    r'^(.+?),\s*University\s+of(?:,\s*(.+))?$', re.IGNORECASE)

def _search_name(school: str) -> str:
    """Return a natural, search-friendly form of the school name.

    Converts storage-format inverted names to the form a human would type:
      'North Carolina, University of'      → 'University of North Carolina'
      'California, University of, Ber'     → 'University of California Ber'
      'Notre Dame, University of'          → 'University of Notre Dame'
    All other names are returned unchanged.
    """
    m = _INVERSION_RE.match(school)
    if m:
        base   = m.group(1).strip()
        suffix = (m.group(2) or '').strip()
        return f"University of {base} {suffix}".strip() if suffix else f"University of {base}"
    return school


def _title_matches(school: str, title: str) -> bool:
    kw = _key_words(_search_name(school))
    if not kw:
        return True
    tl = title.lower()
    return all(k in tl for k in kw)


def _url_mentions_school(school: str, img: dict) -> bool:
    """Return True if the image URL or page URL contains the school's key words.

    Used to filter general-DDG pool/student results that might be from the
    wrong school (e.g. "Clarkson Pool" business instead of Clarkson University).
    Campus/hero images are less sensitive since wrong-school campus shots are
    still usable; pool shots must be from the correct school.
    """
    kw = _key_words(_search_name(school))   # use natural name, no commas
    if not kw:
        return True
    combined = (img.get('url', '') + ' ' + img.get('page_url', '') + ' ' + (img.get('alt') or '')).lower()
    # Any single key word match is sufficient (e.g. 'clarkson' in URL)
    return any(k in combined for k in kw)


def _fname_matches_school(school: str, url: str) -> bool:
    """ALL key words of the search name must appear in the decoded filename.

    Prevents cross-school contamination in Wikimedia Commons results where
    the search API returns wrong-school images (e.g. a photo of the "Duke of
    Mecklenburg" appearing in Duke University's pool search).

    This is stricter than _url_mentions_school (which accepts any single match)
    because Commons filenames nearly always embed the full institution name.
    """
    fname = urllib.parse.unquote(url.split('/')[-1]).lower().replace('_', ' ').replace('-', ' ')
    kw = _key_words(_search_name(school))
    if not kw:
        return True
    return all(k in fname for k in kw)


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
    # Always search with the natural-language name — inverted names like
    # "North Carolina, University of" confuse Wikipedia's search API.
    school = _search_name(school)
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
    """Fetch one page, extract and score all candidate images.

    og:image / twitter:image meta tags are treated as highest-prominence
    candidates and prepended to the result list so they sort to the top.
    """
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

    # ── og:image / twitter:image first (strongest hero signal) ───────────────
    # These are the page author's explicitly chosen hero images — score them
    # high enough to surface above generic wiki_commons resolution giants.
    for og_url in parser.og_images:
        if not og_url or og_url in seen or _is_bad_url(og_url):
            continue
        seen.add(og_url)
        # Base score: page_type weight × 3 + fixed 8.0 hero bonus.
        # visit og:image → 3.0×3+8 = 17.0, home og:image → 1.5×3+8 = 12.5
        base_score = round(PAGE_TYPE_SCORE.get(page_type, 1.0) * 3.0 + 8.0, 3)
        og_img = {
            'url':        og_url,
            'alt':        '',
            'width':      1200,
            'height':     630,
            'prominence': 10,
            'page_type':  page_type,
            'page_url':   page_url,
            'source':     f'web_{page_type}_og',
            'score':      base_score,
        }
        results.append(og_img)

    # ── Regular <img> tags ────────────────────────────────────────────────────
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


def _commons_search(queries: list[str], page_type: str, score_base: float,
                    limit: int = 12, max_results: int = 8,
                    exclude_fname_tokens: set | None = None,
                    min_ratio: float = 0.75) -> list[dict]:
    """Generic Wikimedia Commons image search helper, shared by campus/pool/student functions.

    min_ratio: minimum width/height ratio (0.75 allows near-square pool shots;
    use 1.1 for campus sections that strongly prefer wide/landscape framing).
    """
    results: list[dict] = []
    exclude = exclude_fname_tokens or set()
    for query in queries:
        try:
            params = {
                'action': 'query', 'generator': 'search',
                'gsrnamespace': 6, 'gsrsearch': query,
                'prop': 'imageinfo', 'iiprop': 'url|size|mime',
                'gsrlimit': limit, 'format': 'json', 'formatversion': '2',
            }
            url = COMMONS_API + '?' + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Lane4Recruit/3.0 (swim recruiting; open source)'})
            with urllib.request.urlopen(req, timeout=12) as r:
                data = json.loads(r.read())
            for page in data.get('query', {}).get('pages', []):
                for info in page.get('imageinfo', []):
                    if info.get('mime') in ('image/svg+xml', 'image/gif', 'image/bmp'):
                        continue
                    img_url = info.get('url', '')
                    if not img_url or _is_bad_url(img_url):
                        continue
                    fname = urllib.parse.unquote(img_url).lower()
                    if any(t in fname for t in HISTORICAL_TOKENS):
                        continue
                    if _HIST_YEAR_RE.search(fname):
                        continue
                    if exclude and any(t in fname for t in exclude):
                        continue
                    w = info.get('width', 0)
                    h = info.get('height', 0)
                    if w < MIN_WIDTH or h < MIN_HEIGHT:
                        continue
                    if h > 0 and w / h < min_ratio:
                        continue
                    score = round((w * h) / 1_200_000 + score_base, 3)
                    results.append({
                        'url': img_url, 'source': 'wiki_commons',
                        'width': w, 'height': h, 'score': score,
                        'page_type': page_type, 'page_url': '',
                        'search_context': query,
                    })
        except Exception:
            pass
        if len(results) >= max_results:
            break
    return results


def _wiki_commons_campus(school: str) -> list[dict]:
    """Search Wikimedia Commons for campus building photos, excluding pool facilities."""
    return _commons_search(
        queries=[f'{school} campus', f'{school} university building', f'{school} campus aerial'],
        page_type='campus',
        score_base=1.8,
        limit=20,
        max_results=20,
        # Exclude pool/aquatic facility filenames so they don't leak into campus section
        exclude_fname_tokens=_POOL_FNAME_FILTER,
    )


def _wiki_commons_pool(school: str) -> list[dict]:
    """Search Wikimedia Commons specifically for aquatic facility images."""
    return _commons_search(
        queries=[
            f'{school} swimming pool',
            f'{school} natatorium',
            f'{school} aquatic center',
            f'{school} swim',
        ],
        page_type='swim',
        score_base=2.0,
        limit=20,
        max_results=20,
        min_ratio=0.6,  # pool shots are often near-square — accept them
    )


def _wiki_commons_student(school: str) -> list[dict]:
    """Search Wikimedia Commons for student life images."""
    return _commons_search(
        queries=[
            f'{school} students',
            f'{school} campus life',
            f'{school} student center',
            f'{school} student activities',
        ],
        page_type='student_life',
        score_base=1.5,
        limit=20,
        max_results=20,
        min_ratio=0.75,
    )


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

    seen = {r['url'] for r in results}

    # Three separate purpose-built Commons searches — one per display category.
    # Use _search_name so inverted names ('Notre Dame, University of') become
    # 'University of Notre Dame' before being sent to the Commons search API.
    sq = _search_name(school)
    for fn, pt in [(_wiki_commons_campus, 'campus'),
                   (_wiki_commons_pool,   'swim'),
                   (_wiki_commons_student,'student_life')]:
        for img in fn(sq):
            url = img.get('url', '')
            if not url or url in seen:
                continue
            # Strict filename check: all school keywords must appear in the
            # Commons filename to prevent cross-school contamination
            # (e.g. "Duke of Mecklenburg" → rhinoceros photo appearing for Duke Univ).
            if not _fname_matches_school(school, url):
                continue
            # Pool images additionally need a pool-related token in the filename
            if pt == 'swim':
                fname_low = urllib.parse.unquote(url.split('/')[-1]).lower()
                if not any(t in fname_low for t in _POOL_URL_TOKENS):
                    continue
            results.append(img)
            seen.add(url)

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


def _ddg_image_search(query: str, page_type: str = 'general', n: int = 10) -> list[dict]:
    """DuckDuckGo image search — free, no API key required.

    Returns real web images (athletic department pages, news sites) that are
    school-specific. Used as primary fallback when Google CSE is unavailable.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }
    try:
        # Step 1: fetch the search page to obtain the vqd token
        q_enc = urllib.parse.quote_plus(query)
        url1 = f'https://duckduckgo.com/?q={q_enc}&iax=images&ia=images'
        req1 = urllib.request.Request(url1, headers=headers)
        with urllib.request.urlopen(req1, timeout=12, context=_SSL_CTX) as r:
            html = r.read().decode('utf-8', errors='ignore')
        m = re.search(r'vqd=([\d-]+)', html)
        if not m:
            return []
        vqd = m.group(1)

        # Step 2: fetch image results JSON
        params = {'l': 'us-en', 'o': 'json', 'q': query, 'vqd': vqd, 'f': ',,,,,', 'p': '1'}
        url2 = 'https://duckduckgo.com/i.js?' + urllib.parse.urlencode(params)
        req2 = urllib.request.Request(url2, headers={**headers, 'Referer': url1})
        with urllib.request.urlopen(req2, timeout=12, context=_SSL_CTX) as r:
            data = json.loads(r.read())

        results = []
        for item in data.get('results', [])[:n]:
            link = item.get('image', '')
            if not link or _is_bad_url(link):
                continue
            w = item.get('width', 0)
            h = item.get('height', 0)
            if w < MIN_WIDTH or h < MIN_HEIGHT:
                continue
            if h > 0 and w / h < 0.5:   # allow near-square but reject extreme portrait
                continue
            score = round((w * h) / 1_200_000 + 2.2, 3)
            results.append({
                'url':            link,
                'source':         'ddg_image',
                'width':          w,
                'height':         h,
                'score':          score,
                'page_type':      page_type,
                'page_url':       item.get('url', ''),
                'alt':            item.get('title', ''),
                'search_context': query,
            })
        return results
    except Exception as exc:
        print(f'    [ddg] error for "{query}": {exc}')
        return []


def _google_cse_search(query: str, page_type: str = 'general',
                       n: int = 10, start: int = 1) -> list[dict]:
    """Google Custom Search Engine image search.

    Returns school-specific images from news sites, athletic departments,
    and official pages — far more relevant than Wikimedia Commons.
    `start` can be 1, 11, 21 … for pagination (max 100 results via 10 pages).
    """
    if not GOOGLE_CSE_KEY or not GOOGLE_CSE_ID:
        return []
    params = {
        'key':        GOOGLE_CSE_KEY,
        'cx':         GOOGLE_CSE_ID,
        'q':          query,
        'searchType': 'image',
        'imgType':    'photo',
        'num':        min(n, 10),   # API hard-limits to 10 per request
        'safe':       'medium',
        'start':      start,
    }
    url = GOOGLE_CSE_URL + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Lane4Recruit/3.0'})
    try:
        with urllib.request.urlopen(req, timeout=14) as r:
            data = json.loads(r.read())
        results = []
        for item in data.get('items', []):
            link = item.get('link', '')
            if not link or _is_bad_url(link):
                continue
            img_meta = item.get('image', {})
            w = img_meta.get('width', 0)
            h = img_meta.get('height', 0)
            if w < MIN_WIDTH or h < MIN_HEIGHT:
                continue
            alt  = item.get('title', '')
            page = img_meta.get('contextLink', '')
            # Score: resolution-based + Google-quality bonus (1.0 higher than Commons)
            score = round((w * h) / 1_200_000 + 2.5, 3)
            results.append({
                'url':            link,
                'source':         'google_cse',
                'width':          w,
                'height':         h,
                'score':          score,
                'page_type':      page_type,
                'page_url':       page,
                'alt':            alt,
                'search_context': query,
            })
        return results
    except Exception as exc:
        print(f'    [google_cse] error for "{query}": {exc}')
        return []


# ── Pool management helpers ───────────────────────────────────────────────────

def _category_counts(candidates: list[dict]) -> dict[str, int]:
    """Return count of candidates per category for a school's pool."""
    counts: dict[str, int] = {'campus': 0, 'pool': 0, 'student_life': 0}
    for c in candidates:
        cat = c.get('category', 'campus')
        if cat in counts:
            counts[cat] += 1
    return counts


def _rescore_and_trim_by_category(
    candidates: list[dict],
    per_cat_limit: int = 24,
) -> list[dict]:
    """Dedupe by URL, sort best-first within each category, trim to per_cat_limit each.
    Prevents indefinite accumulation of stale/weak images in the manifest.
    """
    seen_urls: set[str] = set()
    by_cat: dict[str, list[dict]] = {'campus': [], 'pool': [], 'student_life': []}
    other: list[dict] = []

    for c in candidates:
        url = c.get('url', '')
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        cat = c.get('category', 'campus')
        if cat in by_cat:
            by_cat[cat].append(c)
        else:
            other.append(c)

    result: list[dict] = []
    total_trimmed = 0
    for cat, items in by_cat.items():
        items.sort(key=lambda x: x.get('score', 0), reverse=True)
        trimmed_count = max(0, len(items) - per_cat_limit)
        if trimmed_count:
            print(f'    [trim] {cat}: kept {per_cat_limit}/{len(items)} (removed {trimmed_count} weak)')
            total_trimmed += trimmed_count
        result.extend(items[:per_cat_limit])

    result.extend(other)
    return result


# ── Main entry point ──────────────────────────────────────────────────────────

def fetch_candidates(school: str, domains_cache: dict | None = None) -> list[dict]:
    if domains_cache is None:
        domains_cache = _load_domains()

    # sq = search-query name — natural language form used in ALL search queries.
    # 'North Carolina, University of' → 'University of North Carolina'
    # `school` (original) is kept for manifest keys / dedup only.
    sq = _search_name(school)

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
        print(f'    [web] no domain found — using Wikipedia + Google')

    # ── 1b. Site-restricted DDG searches — images guaranteed from this school ─
    # site:school.edu forces DDG to return only images hosted on the school's
    # own servers, eliminating the "random pool from the wrong school" problem.
    if domain:
        # Strip www. so site: covers all subdomains (athletics.duke.edu etc.)
        raw_host = urllib.parse.urlparse(domain).netloc
        domain_host = raw_host.removeprefix('www.')
        _SITE_QUERIES = [
            (f'site:{domain_host} natatorium swimming pool', 'swim'),
            (f'site:{domain_host} aquatic center',           'swim'),
            (f'site:{domain_host} natatorium',               'swim'),
            (f'site:{domain_host} swimming diving',          'swim'),
            (f'site:{domain_host} swim team aquatics',       'swim'),
            (f'site:{domain_host} campus aerial buildings',  'campus'),
            (f'site:{domain_host} campus visit tour',        'campus'),
            (f'site:{domain_host} student life campus',      'student_life'),
            (f'site:{domain_host} students campus life',     'student_life'),
        ]
        print(f'    [ddg_site] site-restricted search on {domain_host}')
        for q, pt in _SITE_QUERIES:
            for img in _ddg_image_search(q, page_type=pt, n=10):
                # Boost: definitely from this school's own website
                img['score'] = round(img.get('score', 2.0) + 1.5, 3)
                _add(img)

    # ── 2. Image search — school-specific images by category ─────────────────
    # Try Google CSE first (best quality); fall back to DuckDuckGo when CSE
    # quota is exhausted or the cx parameter is misconfigured.
    _CATEGORY_QUERIES = [
        (f'{sq} campus',                        'campus'),
        (f'{sq} university buildings campus',   'campus'),
        (f'{sq} natatorium swimming pool',      'swim'),
        (f'{sq} aquatic center swimming',       'swim'),
        (f'{sq} students campus life',          'student_life'),
        (f'{sq} student union college life',    'student_life'),
    ]
    cse_before = len(all_candidates)
    if GOOGLE_CSE_KEY:
        print(f'    [google] fetching campus / pool / student images')
        for q, pt in _CATEGORY_QUERIES:
            for img in _google_cse_search(q, page_type=pt, n=10):
                _add(img)
    if len(all_candidates) == cse_before:
        # CSE returned nothing — use quoted DDG as school-specific fallback
        print(f'    [ddg] fetching campus / pool / student images')
        for category, pt in [('campus', 'campus'), ('pool', 'swim'), ('student_life', 'student_life')]:
            for q_tmpl in _DDG_QUERIES.get(category, []):
                q = q_tmpl.format(school=sq)
                for img in _ddg_image_search(q, page_type=pt, n=10):
                    # Penalise (but keep) images where URL/page/alt doesn't
                    # mention this school — wrong pool, wrong campus, wrong school.
                    if not _url_mentions_school(school, img):
                        img['score'] = round(img.get('score', 2.0) - 1.0, 3)
                    _add(img)

    # ── 3. Wikipedia + Commons (always — supplements both Google and no-Google paths) ──
    wiki_imgs = _wiki_candidates(school)
    wiki_imgs.sort(key=lambda x: x.get('score', 0), reverse=True)
    for img in wiki_imgs:
        # Penalise wiki/commons images where the school name isn't in URL/alt —
        # these are likely misfiled or cross-school images.
        if not _url_mentions_school(school, img):
            img['score'] = round(img.get('score', 2.0) * 0.5, 3)
        _add(img)

    # Pexels removed — it returns the same stock photos for every school,
    # polluting manifests with images that appear across 10+ schools.

    # ── Assign display category to every candidate ───────────────────────────
    for img in all_candidates:
        img['category'] = _assign_category(img)
        if img['category'] == 'pool':
            conf = _pool_confidence(img, school)
            img['pool_confidence'] = round(conf, 2)
            img['pool_attribution'] = _pool_attribution_label(img)
            # Remove very low-confidence pool images (likely wrong school)
            if conf < 0.2:
                img['category'] = 'campus'   # demote rather than discard

    all_candidates.sort(key=lambda x: x.get('score', 0), reverse=True)
    return all_candidates[:MAX_CANDIDATES]


# Per-category Google CSE queries for "More images" — multiple queries + pagination
# give consistently fresh results on every press.
_MORE_GOOGLE_QUERIES: dict[str, list[str]] = {
    'pool': [
        '{school} natatorium swimming pool',
        '{school} aquatic center',
        '{school} natatorium',
        '{school} swim team pool',
        '{school} athletics swimming',
        '{school} swimming pool facility',
        '{school} swimming diving facility',
        '{school} aquatics swim team',
    ],
    'student_life': [
        '{school} students campus',
        '{school} student life',
        '{school} campus life',
        '{school} students studying',
        '{school} student activities',
        '{school} student union campus',
        '{school} college students campus',
        '{school} campus events students',
    ],
    'campus': [
        '{school} campus',
        '{school} university campus',
        '{school} campus aerial',
        '{school} campus quad',
        '{school} campus buildings',
        '{school} campus aerial view',
        '{school} university buildings',
        '{school} campus grounds',
    ],
}

# DDG queries — school name is quoted for exact-match to prevent wrong-school
# results (e.g. "Clarkson Pools" business vs "Clarkson University").
_DDG_QUERIES: dict[str, list[str]] = {
    'pool': [
        '"{school}" natatorium swimming pool',
        '"{school}" aquatic center',
        '"{school}" swimming pool',
        '"{school}" natatorium',
        '"{school}" swim team pool',
        '"{school}" athletics swimming',
        '"{school}" swimming diving pool',
        '"{school}" aquatics swimming',
        '"{school}" swim team pool facility',
    ],
    'student_life': [
        '"{school}" students campus',
        '"{school}" student life',
        '"{school}" campus life',
        '"{school}" students studying',
        '"{school}" student activities',
        '"{school}" college students campus',
        '"{school}" student union',
    ],
    'campus': [
        '"{school}" campus',
        '"{school}" university campus',
        '"{school}" campus aerial',
        '"{school}" campus quad',
        '"{school}" campus buildings',
        '"{school}" university exterior',
    ],
}

_CATEGORY_PAGE_TYPE = {'pool': 'swim', 'student_life': 'student_life', 'campus': 'campus'}


def fetch_candidates_for_category(school: str, category: str,
                                   target: int = 20, city: str | None = None) -> list[dict]:
    """Fetch additional candidates for a specific display category.

    Priority: (1) city-targeted pool queries when city is provided, (2) site-restricted
    DDG on school domain, (3) Google CSE, (4) general DDG fallback (always runs when
    under target — DDG bug fix), (5) Wikimedia Commons.  Deduplicates against the
    existing manifest so every press returns genuinely new images.

    city — school city (e.g. "Walla Walla") used to build targeted pool queries.
    """
    domains_cache = _load_domains()
    existing_urls: set[str] = {c['url'] for c in load_manifest().get(school, [])}
    page_type  = _CATEGORY_PAGE_TYPE.get(category, 'campus')
    new_results: list[dict] = []
    seen: set[str] = set(existing_urls)

    # sq = search-query form of school name (converts inverted names)
    sq = _search_name(school)

    def _add_if_new(item: dict):
        if item.get('url') and item['url'] not in seen:
            seen.add(item['url'])
            new_results.append(item)

    # ── City-targeted pool queries (highest specificity) ─────────────────────
    # Prepend city-based queries for pool so we find the actual facility first.
    base_queries = list(_MORE_GOOGLE_QUERIES.get(category, _MORE_GOOGLE_QUERIES['campus']))
    if category == 'pool' and city:
        city_queries = [
            f'{sq} {city} aquatic center',
            f'{sq} {city} natatorium',
            f'{sq} {city} swimming pool',
            f'{sq} swim team pool',
            f'{sq} athletics swimming',
        ]
        base_queries = city_queries + base_queries

    queries = [q.format(school=sq) if '{school}' in q else q
               for q in base_queries]

    # ── Site-restricted DDG (highest quality — images from school's own site) ─
    domain = domains_cache.get(school) or get_school_domain(school, domains_cache)
    if domain:
        raw_host = urllib.parse.urlparse(domain).netloc
        domain_host = raw_host.removeprefix('www.')
        _SITE_Q = {
            'pool':         [f'site:{domain_host} natatorium swimming pool',
                             f'site:{domain_host} aquatic center',
                             f'site:{domain_host} natatorium',
                             f'site:{domain_host} swimming diving',
                             f'site:{domain_host} swim team aquatics',
                             f'site:{domain_host} recreation center pool',
                             f'site:{domain_host} athletics aquatics'],
            'campus':       [f'site:{domain_host} campus aerial buildings',
                             f'site:{domain_host} campus visit tour',
                             f'site:{domain_host} campus life',
                             f'site:{domain_host} campus'],
            'student_life': [f'site:{domain_host} student life campus',
                             f'site:{domain_host} students campus life',
                             f'site:{domain_host} student activities events'],
        }
        for q in _SITE_Q.get(category, _SITE_Q['campus']):
            if len(new_results) >= target:
                break
            for img in _ddg_image_search(q, page_type=page_type, n=12):
                img['score'] = round(img.get('score', 2.0) + 1.5, 3)
                _add_if_new(img)

    # ── Google CSE (best quality when key + cx are valid) ────────────────────
    if GOOGLE_CSE_KEY and len(new_results) < target:
        for q in queries:
            if len(new_results) >= target:
                break
            for start in (1, 11):  # two pages = up to 20 results per query
                for img in _google_cse_search(q, page_type=page_type, n=10, start=start):
                    _add_if_new(img)
                if len(new_results) >= target:
                    break

    # ── General DDG fallback — ALWAYS runs when still under target ────────────
    # Bug fix: previously only ran when CSE added zero results (wrong — CSE can
    # add 2–3 images and block DDG from filling the remaining 10+ slots).
    if len(new_results) < target:
        ddg_queries = [q.format(school=sq)
                       for q in _DDG_QUERIES.get(category, _DDG_QUERIES['campus'])]
        # Pool gets extra fallback queries for schools without named natatoriums
        if category == 'pool':
            ddg_queries += [
                f'"{sq}" recreation center swimming pool',
                f'"{sq}" athletic facility pool',
                f'"{sq}" varsity pool facility',
                f'"{sq}" swim meet pool',
            ]
        for q in ddg_queries:
            if len(new_results) >= target:
                break
            for img in _ddg_image_search(q, page_type=page_type, n=12):
                # Penalise (but keep) images not mentioning this school
                if not _url_mentions_school(school, img):
                    img['score'] = round(img.get('score', 2.0) - 1.0, 3)
                _add_if_new(img)

    # ── Wikimedia Commons (campus only — supplements DDG results) ────────────
    # Pool and student_life are skipped here: Commons returns generic hotel/resort
    # pool photos and unrelated student images that don't match any specific school.
    # For pool/student_life, DDG site-restricted + DDG fallback are the reliable sources.
    if category == 'campus':
        school_kw = _key_words(school)
        for q in queries:
            if len(new_results) >= 36:
                break
            try:
                params = {
                    'action': 'query', 'generator': 'search',
                    'gsrnamespace': 6, 'gsrsearch': q,
                    'prop': 'imageinfo', 'iiprop': 'url|size|mime',
                    'gsrlimit': 25, 'format': 'json', 'formatversion': '2',
                }
                url = COMMONS_API + '?' + urllib.parse.urlencode(params)
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Lane4Recruit/3.0 (swim recruiting; open source)'})
                with urllib.request.urlopen(req, timeout=14) as r:
                    data = json.loads(r.read())
                for page in data.get('query', {}).get('pages', []):
                    for info in page.get('imageinfo', []):
                        if info.get('mime') in ('image/svg+xml', 'image/gif', 'image/bmp'):
                            continue
                        img_url = info.get('url', '')
                        if not img_url or _is_bad_url(img_url):
                            continue
                        fname = urllib.parse.unquote(img_url).lower()
                        if any(t in fname for t in HISTORICAL_TOKENS):
                            continue
                        if _HIST_YEAR_RE.search(fname):
                            continue
                        # Skip Commons campus images that don't mention this school —
                        # they are unrelated campus shots that happen to rank for the query.
                        if school_kw and not any(k in fname for k in school_kw):
                            continue
                        w = info.get('width', 0)
                        h = info.get('height', 0)
                        if w < MIN_WIDTH or h < MIN_HEIGHT:
                            continue
                        score = round((w * h) / 1_200_000 + 1.5, 3)
                        _add_if_new({
                            'url': img_url, 'source': 'wiki_commons',
                            'width': w, 'height': h, 'score': score,
                            'page_type': page_type, 'page_url': '',
                            'search_context': q,
                        })
            except Exception:
                pass

    # Assign display categories
    for img in new_results:
        img['category'] = _assign_category(img)
        if img['category'] == 'pool':
            conf = _pool_confidence(img, school)
            img['pool_confidence'] = round(conf, 2)
            img['pool_attribution'] = _pool_attribution_label(img)
            if conf < 0.2:
                img['category'] = 'campus'

    new_results.sort(key=lambda x: x.get('score', 0), reverse=True)
    return new_results


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
