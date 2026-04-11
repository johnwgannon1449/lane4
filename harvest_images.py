"""
Lane4 Image Harvester — Google Custom Search Edition
=====================================================
Fetches campus, pool, and students photos for all ~323 schools using the
Google Custom Search JSON API.

Usage:
    python3 harvest_images.py [--school "School Name"] [--reset]

Environment variables required:
    GOOGLE_CSE_KEY  — your Google Custom Search API key
    GOOGLE_CSE_ID   — your Custom Search Engine ID (cx)

Outputs:
    static/school_images.json  — { "School Name": { campus, pool, students } }

The manifest is written after every school so progress is never lost.
Re-running skips schools that already have all three images.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import argparse

MANIFEST_PATH = os.path.join('static', 'data', 'school_images.json')
NAMES_PATH    = 'school_names.json'

# ── Google CSE config ────────────────────────────────────────────────────────

API_KEY = os.environ.get('GOOGLE_CSE_KEY', '')
CX      = os.environ.get('GOOGLE_CSE_ID', '')
CSE_URL = 'https://www.googleapis.com/customsearch/v1'

# ── Image quality filters ────────────────────────────────────────────────────

MIN_WIDTH  = 800
MIN_HEIGHT = 400

# URL fragments that indicate logos, seals, or icons rather than photos
BAD_TOKENS = [
    'seal', 'logo', 'coat_of_arms', 'arms_of', 'crest', 'flag_of',
    'wordmark', 'insignia', 'monogram', 'mascot', 'badge', 'shield',
    'patch', '_mark.', 'icon', 'vector', 'favicon', 'thumbnail',
    'sticker', 'emblem',
]

# Strongly prefer images hosted on .edu domains
EDU_BONUS = 2


def _is_bad_url(url: str) -> bool:
    u = url.lower()
    return any(t in u for t in BAD_TOKENS)


def _score_result(item: dict, prefer_edu: bool) -> float:
    """Higher is better. Returns 0 if the image should be skipped."""
    img  = item.get('image', {})
    link = item.get('link', '')
    mime = item.get('mime', '')
    ctx  = item.get('displayLink', '')

    if mime in ('image/svg+xml', 'image/gif', 'image/bmp'):
        return 0
    if _is_bad_url(link):
        return 0
    w = img.get('width', 0)
    h = img.get('height', 0)
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return 0

    score = w * h / 1_000_000  # megapixels as base score
    if prefer_edu and '.edu' in ctx:
        score += EDU_BONUS
    return score


def _cse_search(query: str, num: int = 8) -> list:
    """Call the Google Custom Search API. Returns list of result items."""
    if not API_KEY or not CX:
        raise RuntimeError('GOOGLE_CSE_KEY and GOOGLE_CSE_ID must be set.')

    params = {
        'key':        API_KEY,
        'cx':         CX,
        'q':          query,
        'searchType': 'image',
        'imgType':    'photo',
        'imgSize':    'large',
        'num':        num,
        'safe':       'active',
    }
    url = CSE_URL + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Lane4Recruit/2.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data.get('items', [])
    except Exception as e:
        print(f'  CSE error: {e}')
        return []


def _best_image(items: list, prefer_edu: bool = True) -> str | None:
    """Return the URL of the highest-scoring image, or None."""
    best_score, best_url = 0.0, None
    for item in items:
        s = _score_result(item, prefer_edu)
        if s > best_score:
            best_score = s
            best_url   = item.get('link')
    return best_url


def fetch_school_images(school: str) -> dict:
    """
    Fetch campus, pool, and students images for one school.
    Returns a dict with keys: campus, pool, students (each a URL or None).
    """
    # Normalise name for queries
    name = school.strip()

    queries = {
        'campus':   f'"{name}" campus buildings exterior',
        'pool':     f'"{name}" swimming pool natatorium aquatic',
        'students': f'"{name}" students campus life',
    }

    result = {}
    for key, q in queries.items():
        print(f'  [{key}] {q}')
        items = _cse_search(q, num=8)
        url   = _best_image(items, prefer_edu=(key != 'pool'))
        result[key] = url
        if url:
            print(f'         ✓ {url[:80]}')
        else:
            print(f'         ✗ no good image found')
        time.sleep(0.5)  # stay well under rate limits

    return result


def load_manifest() -> dict:
    try:
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_manifest(manifest: dict):
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    with open(MANIFEST_PATH, 'w') as f:
        json.dump(manifest, f, indent=2)


def load_school_names() -> list:
    with open(NAMES_PATH) as f:
        return json.load(f)


def needs_update(entry: dict) -> bool:
    """True if any of the three images is missing from an existing entry."""
    if not entry:
        return True
    return not all(entry.get(k) for k in ('campus', 'pool', 'students'))


def run(target_school: str | None = None, reset: bool = False):
    if not API_KEY or not CX:
        print('ERROR: Set GOOGLE_CSE_KEY and GOOGLE_CSE_ID environment variables.')
        sys.exit(1)

    manifest = {} if reset else load_manifest()
    schools  = load_school_names()

    if target_school:
        schools = [s for s in schools if s.lower() == target_school.lower()]
        if not schools:
            print(f'School not found in universe: {target_school}')
            sys.exit(1)

    total   = len(schools)
    done    = 0
    skipped = 0

    for i, school in enumerate(schools, 1):
        existing = manifest.get(school, {})
        if not reset and not needs_update(existing):
            skipped += 1
            continue

        print(f'\n[{i}/{total}] {school}')
        imgs = fetch_school_images(school)
        manifest[school] = imgs
        save_manifest(manifest)
        done += 1

        # Brief pause between schools to avoid rate-limit spikes
        time.sleep(1.0)

    print(f'\nDone. Harvested: {done}, Skipped (already complete): {skipped}')
    print(f'Manifest: {MANIFEST_PATH}  ({len(manifest)} entries)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Harvest school images via Google CSE')
    parser.add_argument('--school', help='Process a single school by name', default=None)
    parser.add_argument('--reset',  help='Clear manifest and restart', action='store_true')
    args = parser.parse_args()
    run(target_school=args.school, reset=args.reset)
