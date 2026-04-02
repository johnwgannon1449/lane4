"""
Lane4 Candidate Image Harvester  (Pexels edition)
==================================================
Fetches up to 8 candidate images per school via the Pexels API and stores
them in static/candidates_manifest.json for admin curation.

Usage:
    python3 harvest_candidates.py [--school "School Name"] [--reset]

Environment variable required:
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

MIN_WIDTH  = 480
MIN_HEIGHT = 270

BAD_TOKENS = [
    'seal', 'logo', 'coat_of_arms', 'crest', 'flag_of', 'wordmark',
    'insignia', 'monogram', 'mascot', 'badge', 'shield', 'patch',
    '_mark.', 'icon', 'vector', 'favicon', 'thumbnail', 'sticker',
    'emblem', 'sprite', 'button', '/buttons/',
]

QUERIES = {
    'campus':   '{name} university campus buildings',
    'pool':     '{name} swimming pool aquatic center',
    'students': '{name} college students campus life',
}

CANDIDATES_PER_QUERY = 5
MAX_CANDIDATES       = 8


def _is_bad_url(url: str) -> bool:
    u = url.lower()
    return any(t in u for t in BAD_TOKENS)


def _score_photo(photo: dict, source: str) -> float:
    w = photo.get('width', 0)
    h = photo.get('height', 0)
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return 0.0
    url = photo.get('src', {}).get('large2x', photo.get('src', {}).get('large', ''))
    if _is_bad_url(url):
        return 0.0
    score = (w * h) / 1_000_000
    if source == 'pool':
        score += 0.5
    return score


def _pexels_search(query: str, per_page: int = 5) -> list:
    if not PEXELS_KEY:
        raise RuntimeError(
            'PEXELS_KEY environment variable is not set. '
            'Get a free key at pexels.com/api and add it as PEXELS_KEY in Replit Secrets.'
        )
    params = {
        'query':       query,
        'per_page':    per_page,
        'page':        1,
        'orientation': 'landscape',
    }
    url = PEXELS_URL + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'Authorization': PEXELS_KEY,
        'User-Agent':    'Lane4Recruit/2.0',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data.get('photos', [])
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode()[:300]
        except Exception:
            pass
        if e.code == 401:
            raise RuntimeError(
                f'Pexels API returned 401 Unauthorized. '
                f'Check that PEXELS_KEY is correct. Detail: {body}'
            )
        if e.code == 429:
            raise RuntimeError('Pexels API rate limit hit (429). Wait a moment and try again.')
        print(f'    Pexels HTTP error {e.code}: {body}')
        return []
    except Exception as e:
        print(f'    Pexels error: {e}')
        return []


def fetch_candidates(school: str) -> list:
    all_candidates = []
    seen_urls = set()

    for source, q_template in QUERIES.items():
        q = q_template.format(name=school)
        print(f'  [{source}] {q}')
        photos = _pexels_search(q, per_page=CANDIDATES_PER_QUERY)
        for photo in photos:
            s = _score_photo(photo, source)
            if s <= 0:
                continue
            src = photo.get('src', {})
            url = src.get('large2x') or src.get('large') or src.get('original', '')
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            all_candidates.append({
                'url':    url,
                'source': source,
                'width':  photo.get('width', 0),
                'height': photo.get('height', 0),
                'score':  round(s, 3),
            })
        time.sleep(0.3)

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
    if not PEXELS_KEY:
        print('ERROR: Set PEXELS_KEY env var. Get a free key at pexels.com/api')
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
        time.sleep(0.5)

    print(f'\nDone. Harvested: {done}  Skipped: {skipped}')
    print(f'Manifest: {CANDIDATES_PATH}  ({len(manifest)} entries)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Harvest candidate images for curation UI')
    parser.add_argument('--school', default=None, help='Process a single school')
    parser.add_argument('--reset',  action='store_true', help='Re-fetch all schools')
    args = parser.parse_args()
    run(target_school=args.school, reset=args.reset)
