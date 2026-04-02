"""
Lane4 Candidate Image Harvester
================================
Fetches up to 8 candidate images per school (across campus, pool, and
students queries) and stores them all in static/candidates_manifest.json.

Unlike harvest_images.py (which picks only the single best image per
category), this script saves every valid candidate so the admin curation
UI can let a human choose.

Usage:
    python3 harvest_candidates.py [--school "School Name"] [--reset]

Environment variables required (same as harvest_images.py):
    GOOGLE_CSE_KEY  — Google Custom Search API key
    GOOGLE_CSE_ID   — Custom Search Engine ID (cx)

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
from datetime import datetime

CANDIDATES_PATH = os.path.join('static', 'candidates_manifest.json')
NAMES_PATH      = 'school_names.json'

API_KEY = os.environ.get('GOOGLE_CSE_KEY', '')
CX      = os.environ.get('GOOGLE_CSE_ID', '')
CSE_URL = 'https://www.googleapis.com/customsearch/v1'

MIN_WIDTH  = 480
MIN_HEIGHT = 270

BAD_TOKENS = [
    'seal', 'logo', 'coat_of_arms', 'crest', 'flag_of', 'wordmark',
    'insignia', 'monogram', 'mascot', 'badge', 'shield', 'patch',
    '_mark.', 'icon', 'vector', 'favicon', 'thumbnail', 'sticker',
    'emblem', 'sprite', 'button', '/buttons/',
]

QUERIES = {
    'campus':   '{name} campus exterior buildings',
    'pool':     '{name} swimming pool natatorium aquatic center',
    'students': '{name} students campus life quad',
}

CANDIDATES_PER_QUERY = 8
MAX_CANDIDATES       = 8  # total kept per school (best-scored across all queries)


def _is_bad_url(url: str) -> bool:
    u = url.lower()
    return any(t in u for t in BAD_TOKENS)


def _score(item: dict) -> float:
    img  = item.get('image', {})
    link = item.get('link', '')
    mime = item.get('mime', '')
    ctx  = item.get('displayLink', '')

    if mime in ('image/svg+xml', 'image/gif', 'image/bmp', 'image/webp'):
        pass  # allow webp
    if mime in ('image/svg+xml', 'image/gif', 'image/bmp'):
        return 0.0
    if _is_bad_url(link):
        return 0.0

    w = img.get('width', 0)
    h = img.get('height', 0)
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return 0.0

    score = (w * h) / 1_000_000
    if '.edu' in ctx:
        score += 1.5
    return score


def _cse_search(query: str, num: int = 8) -> list:
    if not API_KEY or not CX:
        raise RuntimeError('GOOGLE_CSE_KEY and GOOGLE_CSE_ID must be set.')
    params = {
        'key': API_KEY, 'cx': CX,
        'q': query, 'searchType': 'image',
        'imgType': 'photo', 'imgSize': 'large',
        'num': num, 'safe': 'active',
    }
    url = CSE_URL + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Lane4Recruit/2.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data.get('items', [])
    except urllib.error.HTTPError as e:
        body = ''
        try: body = e.read().decode()[:300]
        except Exception: pass
        if e.code == 403:
            raise RuntimeError(
                f'Google CSE API returned 403 Forbidden. '
                f'Check: (1) Custom Search JSON API is enabled in Google Cloud Console, '
                f'(2) GOOGLE_CSE_KEY is valid, (3) GOOGLE_CSE_ID is correct. '
                f'Detail: {body}'
            )
        if e.code == 429:
            raise RuntimeError(f'Google CSE API quota exceeded (429). Wait and try again.')
        print(f'    CSE HTTP error {e.code}: {body}')
        return []
    except Exception as e:
        print(f'    CSE error: {e}')
        return []


def fetch_candidates(school: str) -> list:
    all_candidates = []
    seen_urls = set()

    for source, q_template in QUERIES.items():
        q = q_template.format(name=school)
        print(f'  [{source}] {q}')
        items = _cse_search(q, num=CANDIDATES_PER_QUERY)
        for item in items:
            s = _score(item)
            if s <= 0:
                continue
            url = item.get('link', '')
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            img = item.get('image', {})
            all_candidates.append({
                'url':    url,
                'source': source,
                'width':  img.get('width', 0),
                'height': img.get('height', 0),
                'score':  round(s, 3),
            })
        time.sleep(0.4)

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
    if not API_KEY or not CX:
        print('ERROR: Set GOOGLE_CSE_KEY and GOOGLE_CSE_ID env vars.')
        sys.exit(1)

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
        time.sleep(0.8)

    print(f'\nDone. Harvested: {done}  Skipped: {skipped}')
    print(f'Manifest: {CANDIDATES_PATH}  ({len(manifest)} entries)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Harvest candidate images for curation UI')
    parser.add_argument('--school', default=None, help='Process a single school')
    parser.add_argument('--reset',  action='store_true', help='Re-fetch all schools')
    args = parser.parse_args()
    run(target_school=args.school, reset=args.reset)
