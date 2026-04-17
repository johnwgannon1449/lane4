"""services/pregen.py — Pre-generation service for cached deep dive sections.

Generates four section types lazily on first request per school or school-program pair:
  school_content_cache   : What School Is Known For, Campus Life (main + More)
  program_content_cache  : Academic Program (main + More: Going Deeper), Minor

Model: claude-haiku-4-5-20251001 with web_search_20250305 enabled.
Cache: reads before generating, writes after, keyed by school_name or (school_name, major, minor).
"""

import concurrent.futures as _futures

from db import get_db, using_sqlite
from ai_client import _get_anthropic
from prompts_config import (
    SCHOOL_KNOWN_FOR_PROMPT,
    CAMPUS_LIFE_PROMPT,
    MAJOR_PROMPT,
    MINOR_PROMPT,
)

_HAIKU_MODEL       = 'claude-haiku-4-5-20251001'
_WEB_SEARCH        = {'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 5}
_CALL_TIMEOUT_SECS = 60


# ---------------------------------------------------------------------------
# Internal: Haiku API call with web search agentic loop
# ---------------------------------------------------------------------------

def _call_haiku(system: str, user_message: str) -> str:
    """Call Haiku 4.5 with web search. Runs the tool loop until end_turn.

    Each API round is capped at _CALL_TIMEOUT_SECS. TimeoutError propagates
    to the caller (get_or_generate_*), which catches it and returns empty
    strings so the deep dive falls back to live generation for those sections.
    """
    client = _get_anthropic()
    if not client:
        raise RuntimeError('ANTHROPIC_API_KEY not configured')

    messages = [{'role': 'user', 'content': user_message}]

    def _single_round(msgs):
        return client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=4000,
            system=system,
            tools=[_WEB_SEARCH],
            messages=msgs,
        )

    for _ in range(10):  # safety cap on tool rounds
        with _futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_single_round, list(messages))
            try:
                response = future.result(timeout=_CALL_TIMEOUT_SECS)
            except _futures.TimeoutError:
                print(f'[pregen] Haiku API call timed out after {_CALL_TIMEOUT_SECS}s')
                raise TimeoutError(f'Haiku call exceeded {_CALL_TIMEOUT_SECS}s')

        text = ''.join(b.text for b in response.content if hasattr(b, 'text'))

        if response.stop_reason == 'end_turn':
            return text

        if response.stop_reason == 'tool_use':
            # Append assistant turn, then send tool_results to continue.
            # web_search_20250305 is server-side: Anthropic executes the search
            # and returns results in the tool_use block's content attribute.
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
            return text

    return ''


# ---------------------------------------------------------------------------
# Internal: response parsers
# ---------------------------------------------------------------------------

_SEARCH_NARRATION_PREFIXES = (
    "i'll search",
    "let me search",
    "now let me search",
    "based on my research",
    "based on the search",
    "i'll look",
    "let me look",
    "i'll find",
    "searching for",
    "i've searched",
    "i've found",
    "my research shows",
    "from my research",
)

def _strip_narration(text: str) -> str:
    """Remove lines that are search narration leaking into generated output.

    Matches lines whose lowercased content starts with any prefix in
    _SEARCH_NARRATION_PREFIXES. Logs each removed line for visibility.
    """
    clean_lines = []
    for line in text.splitlines():
        lowered = line.strip().lower()
        if any(lowered.startswith(p) for p in _SEARCH_NARRATION_PREFIXES):
            print(f'[pregen] stripped narration line: {line.strip()!r}')
        else:
            clean_lines.append(line)
    return '\n'.join(clean_lines).strip()


def _split_on_heading(text: str, heading: str) -> tuple:
    """Split text into (before_heading, from_heading_onward). Case-sensitive."""
    idx = text.find(heading)
    if idx == -1:
        return text.strip(), ''
    return text[:idx].strip(), text[idx:].strip()


# ---------------------------------------------------------------------------
# Internal: generation functions
# ---------------------------------------------------------------------------

def _generate_known_for(school_name: str, division: str, conference: str,
                         region: str, school_type: str) -> str:
    user_msg = (
        f"School: {school_name}\n"
        f"Division: {division}\n"
        f"Conference: {conference}\n"
        f"Region: {region}\n"
        f"Type: {school_type}\n\n"
        f"Research {school_name} using web search, then write the "
        f"'What School Is Known For' section following the rules and exemplar above."
    )
    return _strip_narration(_call_haiku(SCHOOL_KNOWN_FOR_PROMPT, user_msg))


def _generate_campus_life(school_name: str, division: str, conference: str,
                           region: str, school_type: str, meta: dict) -> tuple:
    meta_lines = '\n'.join(
        f"  {k}: {v}" for k, v in (meta or {}).items() if v
    )
    user_msg = (
        f"School: {school_name}\n"
        f"Division: {division}\n"
        f"Conference: {conference}\n"
        f"Location: {region}\n"
        f"Type: {school_type}\n"
        + (f"SCHOOL_META:\n{meta_lines}\n" if meta_lines else '')
        + f"\nResearch {school_name} using web search (Reddit, Niche, student reviews, "
          f"school paper), then write the Campus Life section and the "
          f"'More: Life Outside the Pool' expansion following the rules and exemplar above."
    )
    raw = _strip_narration(_call_haiku(CAMPUS_LIFE_PROMPT, user_msg))
    return _split_on_heading(raw, 'More: Life Outside the Pool')


def _generate_major(school_name: str, division: str, major: str) -> tuple:
    user_msg = (
        f"School: {school_name}\n"
        f"Division: {division}\n"
        f"Program: {major}\n\n"
        f"Research the {major} program at {school_name} using web search "
        f"(department page, faculty research, career outcomes, student voice), "
        f"then write the Academic Program section and 'More: Going Deeper' expansion "
        f"following the rules and exemplars above."
    )
    raw = _strip_narration(_call_haiku(MAJOR_PROMPT, user_msg))
    return _split_on_heading(raw, 'More: Going Deeper')


def _generate_minor(school_name: str, major: str, minor: str) -> str:
    user_msg = (
        f"School: {school_name}\n"
        f"Minor: {minor}\n"
        f"Primary Major: {major}\n\n"
        f"Research the {minor} minor at {school_name} using web search "
        f"(academic catalog, department pages), then write the Minor section "
        f"following the rules above."
    )
    return _strip_narration(_call_haiku(MINOR_PROMPT, user_msg))


# ---------------------------------------------------------------------------
# Internal: cache read/write
# ---------------------------------------------------------------------------

def _read_school_cache(school_name: str):
    """Return cached school content dict or None if not cached."""
    try:
        with get_db() as conn:
            if using_sqlite():
                cur = conn.cursor()
                cur.execute(
                    'SELECT known_for, campus_life_main, campus_life_more '
                    'FROM school_content_cache WHERE school_name = ?',
                    (school_name,)
                )
                row = cur.fetchone()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        'SELECT known_for, campus_life_main, campus_life_more '
                        'FROM school_content_cache WHERE school_name = %s',
                        (school_name,)
                    )
                    row = cur.fetchone()
        if row is None:
            return None
        return {
            'known_for':        row[0] or '',
            'campus_life_main': row[1] or '',
            'campus_life_more': row[2] or '',
        }
    except Exception as e:
        print(f'[pregen] school cache read error: {e}')
        return None


def _write_school_cache(school_name: str, content: dict) -> None:
    try:
        with get_db() as conn:
            if using_sqlite():
                with conn:
                    conn.execute(
                        'INSERT INTO school_content_cache '
                        '(school_name, known_for, campus_life_main, campus_life_more) '
                        'VALUES (?, ?, ?, ?) '
                        'ON CONFLICT(school_name) DO UPDATE SET '
                        'known_for = excluded.known_for, '
                        'campus_life_main = excluded.campus_life_main, '
                        'campus_life_more = excluded.campus_life_more, '
                        'generated_at = CURRENT_TIMESTAMP',
                        (school_name,
                         content.get('known_for', ''),
                         content.get('campus_life_main', ''),
                         content.get('campus_life_more', ''))
                    )
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        'INSERT INTO school_content_cache '
                        '(school_name, known_for, campus_life_main, campus_life_more) '
                        'VALUES (%s, %s, %s, %s) '
                        'ON CONFLICT(school_name) DO UPDATE SET '
                        'known_for = EXCLUDED.known_for, '
                        'campus_life_main = EXCLUDED.campus_life_main, '
                        'campus_life_more = EXCLUDED.campus_life_more, '
                        'generated_at = NOW()',
                        (school_name,
                         content.get('known_for', ''),
                         content.get('campus_life_main', ''),
                         content.get('campus_life_more', ''))
                    )
                conn.commit()
    except Exception as e:
        print(f'[pregen] school cache write error: {e}')


def _read_program_cache(school_name: str, major: str, minor: str):
    """Return cached program content dict or None if not cached."""
    minor_key = (minor or '').strip()
    try:
        with get_db() as conn:
            if using_sqlite():
                cur = conn.cursor()
                cur.execute(
                    'SELECT academic_program_main, academic_program_more, minor_content '
                    'FROM program_content_cache '
                    'WHERE school_name = ? AND major = ? AND minor = ?',
                    (school_name, major, minor_key)
                )
                row = cur.fetchone()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        'SELECT academic_program_main, academic_program_more, minor_content '
                        'FROM program_content_cache '
                        'WHERE school_name = %s AND major = %s AND minor = %s',
                        (school_name, major, minor_key)
                    )
                    row = cur.fetchone()
        if row is None:
            return None
        return {
            'academic_program_main': row[0] or '',
            'academic_program_more': row[1] or '',
            'minor_content':         row[2] or '',
        }
    except Exception as e:
        print(f'[pregen] program cache read error: {e}')
        return None


def _write_program_cache(school_name: str, major: str, minor: str,
                          content: dict) -> None:
    minor_key = (minor or '').strip()
    try:
        with get_db() as conn:
            if using_sqlite():
                with conn:
                    conn.execute(
                        'INSERT INTO program_content_cache '
                        '(school_name, major, minor, academic_program_main, '
                        ' academic_program_more, minor_content) '
                        'VALUES (?, ?, ?, ?, ?, ?) '
                        'ON CONFLICT(school_name, major, minor) DO UPDATE SET '
                        'academic_program_main = excluded.academic_program_main, '
                        'academic_program_more = excluded.academic_program_more, '
                        'minor_content = excluded.minor_content, '
                        'generated_at = CURRENT_TIMESTAMP',
                        (school_name, major, minor_key,
                         content.get('academic_program_main', ''),
                         content.get('academic_program_more', ''),
                         content.get('minor_content', ''))
                    )
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        'INSERT INTO program_content_cache '
                        '(school_name, major, minor, academic_program_main, '
                        ' academic_program_more, minor_content) '
                        'VALUES (%s, %s, %s, %s, %s, %s) '
                        'ON CONFLICT(school_name, major, minor) DO UPDATE SET '
                        'academic_program_main = EXCLUDED.academic_program_main, '
                        'academic_program_more = EXCLUDED.academic_program_more, '
                        'minor_content = EXCLUDED.minor_content, '
                        'generated_at = NOW()',
                        (school_name, major, minor_key,
                         content.get('academic_program_main', ''),
                         content.get('academic_program_more', ''),
                         content.get('minor_content', ''))
                    )
                conn.commit()
    except Exception as e:
        print(f'[pregen] program cache write error: {e}')


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_or_generate_school_content(school_name: str, division: str = '',
                                    conference: str = '', region: str = '',
                                    school_type: str = '', meta: dict = None) -> dict:
    """Return school-level cached content, generating and caching on first miss.

    Returns dict with keys: known_for, campus_life_main, campus_life_more.
    Any key may be an empty string if generation fails.
    """
    cached = _read_school_cache(school_name)
    if cached:
        print(f'[pregen] school cache hit: {school_name}')
        return cached

    print(f'[pregen] school cache miss, generating: {school_name}')
    try:
        known_for = _generate_known_for(school_name, division, conference,
                                        region, school_type)
        campus_main, campus_more = _generate_campus_life(school_name, division,
                                                          conference, region,
                                                          school_type, meta or {})
    except Exception as e:
        print(f'[pregen] generation error for {school_name}: {e}')
        return {'known_for': '', 'campus_life_main': '', 'campus_life_more': ''}

    content = {
        'known_for':        known_for,
        'campus_life_main': campus_main,
        'campus_life_more': campus_more,
    }
    _write_school_cache(school_name, content)
    return content


def get_or_generate_program_content(school_name: str, major: str,
                                     minor: str = '', division: str = '') -> dict:
    """Return program-level cached content, generating and caching on first miss.

    Returns dict with keys: academic_program_main, academic_program_more, minor_content.
    Any key may be an empty string if generation fails or inputs are absent.
    """
    if not major:
        return {'academic_program_main': '', 'academic_program_more': '',
                'minor_content': ''}

    cached = _read_program_cache(school_name, major, minor)
    if cached:
        print(f'[pregen] program cache hit: {major} at {school_name}')
        return cached

    print(f'[pregen] program cache miss, generating: {major} at {school_name}')
    try:
        acad_main, acad_more = _generate_major(school_name, division, major)
        minor_content = _generate_minor(school_name, major, minor) if minor else ''
    except Exception as e:
        print(f'[pregen] generation error for {major} at {school_name}: {e}')
        return {'academic_program_main': '', 'academic_program_more': '',
                'minor_content': ''}

    content = {
        'academic_program_main': acad_main,
        'academic_program_more': acad_more,
        'minor_content':         minor_content,
    }
    _write_program_cache(school_name, major, minor, content)
    return content
