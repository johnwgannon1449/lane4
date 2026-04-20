#!/usr/bin/env python3
"""scripts/pregen_all_schools.py — Batch pre-generate all school content for 14 schools.

Generates and caches:
  - school_content_cache: Known For + Campus Life (main + More) + Cost data  (14 items)
  - program_content_cache: Academic Program (main + More: Going Deeper) per major (28 items)
  - minor_content_cache: Minor paragraph per minor (14 items)

Total target: 56 items. Takes ~20 minutes. Run once before sleep.

Usage:
    python scripts/pregen_all_schools.py

Requires: DATABASE_URL and ANTHROPIC_API_KEY environment variables.
"""
import os
import sys
import json
import time
import concurrent.futures as _futures
from dotenv import load_dotenv

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

try:
    import psycopg2
except ImportError:
    print('ERROR: psycopg2 not installed. Run: pip install psycopg2-binary')
    sys.exit(1)

try:
    import anthropic
except ImportError:
    print('ERROR: anthropic not installed. Run: pip install anthropic')
    sys.exit(1)

# ---------------------------------------------------------------------------
# Target schools and programs
# ---------------------------------------------------------------------------

SCHOOLS = [
    'Johns Hopkins University',
    'Worcester Polytechnic Institute',
    'Tufts University',
    'Macalester College',
    'University of Chicago',
    'Carnegie Mellon University',
    'Swarthmore College',
    'Rose-Hulman Institute of Technology',
    'Washington University in St. Louis',
    'University of Rochester',
    'Gettysburg College',
    'Whitman College',
    'Harvey Mudd College',
    'Pomona College',
]

MAJORS = ['Biomedical Engineering', 'Astrophysics']
MINORS = ['Biomedical Engineering', 'Astrophysics']

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_PREGEN_MODEL = 'claude-sonnet-4-6'
_WEB_SEARCH   = {'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 5}
_PROMPTS_DIR  = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prompts')

_NARRATION_PREFIXES = (
    "i'll search", "let me search", "now let me search",
    "based on my research", "based on the search",
    "i'll look", "let me look", "i'll find",
    "searching for", "i've searched", "i've found",
    "my research shows", "from my research",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_prompt(filename: str) -> str:
    """Load a prompt file from the prompts/ directory. Fresh read every call."""
    path = os.path.join(_PROMPTS_DIR, filename)
    with open(path, encoding='utf-8') as f:
        return f.read().strip()


def _db():
    """Get a fresh PostgreSQL connection from DATABASE_URL."""
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError('DATABASE_URL environment variable is not set')
    return psycopg2.connect(db_url, connect_timeout=10)


def _strip_narration(text: str) -> str:
    """Remove search-narration lines that leak into generated output."""
    clean = []
    for line in text.splitlines():
        if not any(line.strip().lower().startswith(p) for p in _NARRATION_PREFIXES):
            clean.append(line)
    return '\n'.join(clean).strip()


def _split_on_heading(text: str, heading: str):
    """Split text into (before_heading, from_heading). Returns (text, '') if not found."""
    idx = text.find(heading)
    if idx == -1:
        return text.strip(), ''
    return text[:idx].strip(), text[idx:].strip()


def _call_claude(system: str, user_message: str, max_tokens: int = 2000) -> str:
    """Call Claude Sonnet with web search. Runs the tool loop until end_turn."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY environment variable is not set')

    client = anthropic.Anthropic(api_key=api_key)
    messages = [{'role': 'user', 'content': user_message}]

    def _single_round(msgs):
        return client.messages.create(
            model=_PREGEN_MODEL,
            max_tokens=max_tokens,
            system=system,
            tools=[_WEB_SEARCH],
            messages=msgs,
        )

    for _round in range(10):  # safety cap on tool-use rounds
        with _futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_single_round, list(messages))
            try:
                response = future.result(timeout=120)
            except _futures.TimeoutError:
                raise TimeoutError('Claude API call timed out after 120s')

        text = ''.join(b.text for b in response.content if hasattr(b, 'text'))

        if response.stop_reason == 'end_turn':
            return text

        if response.stop_reason == 'tool_use':
            messages.append({'role': 'assistant', 'content': response.content})
            tool_results = []
            for block in response.content:
                if block.type == 'tool_use':
                    tool_results.append({
                        'type': 'tool_result',
                        'tool_use_id': block.id,
                        'content': getattr(block, 'content', []),
                    })
            if tool_results:
                messages.append({'role': 'user', 'content': tool_results})
        else:
            return text  # unexpected stop reason

    return ''  # hit round cap without end_turn


# ---------------------------------------------------------------------------
# School-level content
# ---------------------------------------------------------------------------

def _pregen_school_content(school_name: str) -> None:
    """Generate Known For + Campus Life + Cost data and cache in school_content_cache."""
    print(f'    [known_for]   {school_name}')
    known_for = _strip_narration(_call_claude(
        _load_prompt('school_known_for_prompt.txt'),
        f"School: {school_name}\n\nResearch {school_name} using web search, then write the "
        f"'What School Is Known For' section following the rules and exemplar above.",
        max_tokens=600,
    ))
    time.sleep(10)  # Rate limit - increased to avoid 429 errors

    print(f'    [campus_life] {school_name}')
    campus_raw = _strip_narration(_call_claude(
        _load_prompt('campus_life_prompt.txt'),
        f"School: {school_name}\n\nResearch {school_name} campus life using web search "
        f"(Reddit, Niche, student reviews, school paper), then write the Campus Life section "
        f"and the 'More: Life Outside the Pool' expansion following the rules and exemplar above.",
        max_tokens=1200,
    ))
    campus_main, campus_more = _split_on_heading(campus_raw, 'More: Life Outside the Pool')
    time.sleep(10)  # Rate limit - increased to avoid 429 errors

    print(f'    [cost_data]   {school_name}')
    cost_json_raw = _strip_narration(_call_claude(
        """You are a research assistant extracting cost data for a college planning tool.
Use web search to find current official data for the school provided.
Return ONLY a JSON object with exactly these fields — no preamble, no explanation, no markdown:

{
  "coa": <integer: total annual cost of attendance in dollars, rounded to nearest 1000>,
  "merit_offered": <boolean: true if this school offers merit scholarships to incoming freshmen>,
  "merit_range_low": <integer or null: minimum annual merit scholarship amount>,
  "merit_range_high": <integer or null: maximum annual merit scholarship amount>,
  "merit_notes": <string or null: names and amounts of notable named scholarships, null if none>,
  "need_based_headline": <string or null: ONLY a specific, notable need-based policy worth calling out.
    Good: "Families earning under $75,000 pay zero tuition." "Meets 100% of demonstrated need."
    Bad (use null): "Offers need-based financial aid." If no notable policy exists, use null.>
}

Return only valid JSON. No other text.""",
        f"School: {school_name}\n\nUse web search to find current cost of attendance and "
        f"financial aid data for {school_name}. Return the JSON object as specified.",
        max_tokens=400,
    ))
    time.sleep(10)  # Rate limit - increased to avoid 429 errors

    cost_data = {}
    try:
        raw = cost_json_raw.strip()
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]
        data = json.loads(raw)
        cost_data = {
            'coa':                 int(data.get('coa') or 0) or None,
            'merit_offered':       bool(data.get('merit_offered', False)),
            'merit_range_low':     int(data['merit_range_low']) if data.get('merit_range_low') else None,
            'merit_range_high':    int(data['merit_range_high']) if data.get('merit_range_high') else None,
            'merit_notes':         str(data['merit_notes']) if data.get('merit_notes') else None,
            'need_based_headline': str(data['need_based_headline']) if data.get('need_based_headline') else None,
        }
    except Exception as e:
        print(f'    [cost_data]   PARSE ERROR for {school_name}: {e}')

    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO school_content_cache
                    (school_name, known_for, campus_life_main, campus_life_more,
                     coa, merit_offered, merit_range_low, merit_range_high,
                     merit_notes, need_based_headline, generated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (school_name) DO UPDATE SET
                    known_for          = EXCLUDED.known_for,
                    campus_life_main   = EXCLUDED.campus_life_main,
                    campus_life_more   = EXCLUDED.campus_life_more,
                    coa                = EXCLUDED.coa,
                    merit_offered      = EXCLUDED.merit_offered,
                    merit_range_low    = EXCLUDED.merit_range_low,
                    merit_range_high   = EXCLUDED.merit_range_high,
                    merit_notes        = EXCLUDED.merit_notes,
                    need_based_headline = EXCLUDED.need_based_headline,
                    generated_at       = NOW()
            """, (
                school_name,
                known_for,
                campus_main,
                campus_more,
                cost_data.get('coa'),
                cost_data.get('merit_offered'),
                cost_data.get('merit_range_low'),
                cost_data.get('merit_range_high'),
                cost_data.get('merit_notes'),
                cost_data.get('need_based_headline'),
            ))
        conn.commit()
        print(f'    [cached]      school: {school_name}')
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Program-level content
# ---------------------------------------------------------------------------

def _pregen_program_content(school_name: str, major: str) -> None:
    """Generate Academic Program (main + More: Going Deeper) and cache in program_content_cache."""
    print(f'    [program]     {major} at {school_name}')
    raw = _strip_narration(_call_claude(
        _load_prompt('major_prompt.txt'),
        f"School: {school_name}\nProgram: {major}\n\n"
        f"Research the {major} program at {school_name} using web search "
        f"(department page, faculty research, career outcomes, student voice), "
        f"then write the Academic Program section and 'More: Going Deeper' expansion "
        f"following the rules and exemplars above.",
        max_tokens=1500,
    ))

    main_content, more_content = _split_on_heading(raw, 'More: Going Deeper')

    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO program_content_cache
                    (school_name, major, minor, academic_program_main, academic_program_more, generated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (school_name, major, minor) DO UPDATE SET
                    academic_program_main = EXCLUDED.academic_program_main,
                    academic_program_more = EXCLUDED.academic_program_more,
                    generated_at          = NOW()
            """, (school_name, major, '', main_content, more_content))
        conn.commit()
        print(f'    [cached]      program: {major} at {school_name}')
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Minor-level content
# ---------------------------------------------------------------------------

def _pregen_minor_content(school_name: str, minor: str) -> None:
    """Generate minor paragraph and cache in minor_content_cache."""
    print(f'    [minor]       {minor} at {school_name}')
    content = _strip_narration(_call_claude(
        _load_prompt('minor_prompt.txt'),
        f"School: {school_name}\nMinor: {minor}\n\n"
        f"Research the {minor} minor at {school_name} using web search "
        f"(academic catalog, department pages), then write the Minor section "
        f"following the rules above.",
        max_tokens=400,
    ))

    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO minor_content_cache
                    (school_name, minor_name, minor_content, generated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (school_name, minor_name) DO UPDATE SET
                    minor_content = EXCLUDED.minor_content,
                    generated_at  = NOW()
            """, (school_name, minor, content))
        conn.commit()
        print(f'    [cached]      minor: {minor} at {school_name}')
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    SCHOOLS = [
        'Worcester Polytechnic Institute',
        'Rose-Hulman Institute of Technology',
    ]

    print('=' * 65)
    print('LANE4 PRE-GENERATION SCRIPT')
    print(f'Schools: {len(SCHOOLS)}  |  Majors: {len(MAJORS)}  |  Minors: {len(MINORS)}')
    total_target = len(SCHOOLS) + len(SCHOOLS) * len(MAJORS) + len(SCHOOLS) * len(MINORS)
    print(f'Target items: {total_target}')
    print('=' * 65)

    start_time   = time.time()
    total_cached = 0
    errors       = []

    for i, school in enumerate(SCHOOLS, 1):
        print(f'\n[{i:02d}/{len(SCHOOLS)}] {school}')

        # School-level content (known_for + campus_life + cost)
        try:
            _pregen_school_content(school)
            total_cached += 1
        except Exception as e:
            msg = f'{school} [school]: {e}'
            print(f'    ERROR: {msg}')
            errors.append(msg)

        # Program content for each major
        for major in MAJORS:
            time.sleep(10)  # Rate limit - increased to avoid 429 errors
            try:
                _pregen_program_content(school, major)
                total_cached += 1
            except Exception as e:
                msg = f'{school} [{major}]: {e}'
                print(f'    ERROR: {msg}')
                errors.append(msg)

        # Minor content for each minor
        for minor in MINORS:
            time.sleep(10)  # Rate limit - increased to avoid 429 errors
            try:
                _pregen_minor_content(school, minor)
                total_cached += 1
            except Exception as e:
                msg = f'{school} [minor {minor}]: {e}'
                print(f'    ERROR: {msg}')
                errors.append(msg)

    elapsed = time.time() - start_time
    print('\n' + '=' * 65)
    print('PRE-GENERATION COMPLETE')
    print(f'Items cached : {total_cached} / {total_target}')
    print(f'Total time   : {elapsed / 60:.1f} minutes')
    if errors:
        print(f'Errors ({len(errors)}):')
        for err in errors:
            print(f'  - {err}')
    else:
        print('No errors.')
    print('=' * 65)


if __name__ == '__main__':
    main()
