"""routes/deep_dive.py — Deep dive and coach-email content generation endpoints."""
import os
import re
import json
import threading

from flask import Blueprint, request, jsonify, Response, stream_with_context
from ai_client import _get_anthropic

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'prompts')


def _load_prompt(filename: str) -> str:
    """Load a prompt file fresh from disk on every call. No caching."""
    path = os.path.join(_PROMPTS_DIR, filename)
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f'[deep_dive] prompt file not found: {filename}')
        return ''


def _read_minor_cache(school_name: str, minor_name: str) -> str:
    """Read minor content from minor_content_cache. Returns '' on miss."""
    if not school_name or not minor_name:
        return ''
    try:
        from db import get_db, using_sqlite
        with get_db() as conn:
            if using_sqlite():
                cur = conn.cursor()
                cur.execute(
                    'SELECT minor_content FROM minor_content_cache '
                    'WHERE school_name = ? AND minor_name = ?',
                    (school_name, minor_name)
                )
                row = cur.fetchone()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        'SELECT minor_content FROM minor_content_cache '
                        'WHERE school_name = %s AND minor_name = %s',
                        (school_name, minor_name)
                    )
                    row = cur.fetchone()
        return (row[0] or '') if row else ''
    except Exception as e:
        print(f'[deep_dive] minor_content_cache read error: {e}')
        return ''
from services.pregen import (
    read_school_cache, read_program_cache,
    get_or_generate_school_content, get_or_generate_program_content,
)
from models.swimmer_defaults import JAMES
from models.school_data import _oou_lookup, SCHOOL_NAME_ALIASES
from scoring.admission import _oou_admission
from scoring.universe import build_school_universe
from search.filters import _program_strength_desc
from search.prompts import _build_top3_text, _build_vibe_lines
from deep_dive.merit import _act_to_sat, _estimate_merit_block

deep_dive_bp = Blueprint('deep_dive', __name__)


@deep_dive_bp.route('/api/deep-dive/academic', methods=['POST'])
def deep_dive_academic():
    """
    Lazy-load the "More about this program" academic expansion for a school.

    Body: { school, primaryMajor, location, schoolVibe }
    Response: { body: "plain prose" } or { error }
    """
    data         = request.json or {}
    school_name  = data.get('school', '').strip()
    major        = data.get('primaryMajor', '').strip()
    location     = data.get('location', '').strip()
    vibe         = data.get('schoolVibe', '').strip()

    if not school_name or not major:
        return jsonify({'error': 'school and primaryMajor are required'}), 400

    client = _get_anthropic()
    if not client:
        return jsonify({'error': 'AI is not configured — add ANTHROPIC_API_KEY to enable deep dives'}), 200

    system_prompt = (
        "You are an experienced college advisor who understands how academic programs work at universities.\n\n"
        "Your job is to explain an academic program clearly to a student and their parents so they understand "
        "how the program actually works and what the experience would be like.\n\n"
        "This content appears in the 'More about this program' expansion inside a school Deep Dive. "
        "The summary Deep Dive already introduced the school, so this section should add new insight "
        "rather than repeat information.\n\n"
        "GOAL\n"
        "Help the reader understand the structure and realities of the academic program. Focus on details "
        "a student might not immediately learn from a quick look at the school's website.\n\n"
        "THINK FIRST\n"
        "Before writing, briefly identify 3-5 distinctive aspects of the program that a student might "
        "not already know. These might include program structure, unusual pathways, research access, "
        "cross-registration opportunities, internship patterns, or career outcomes. "
        "Use those insights to guide the explanation. Do not output the list.\n\n"
        "WRITING STYLE\n"
        "Write like an experienced college advisor explaining the program to a student and parent.\n"
        "The tone should be: knowledgeable, clear, natural, engaging.\n"
        "Avoid sounding like: a marketing brochure, an academic paper, an AI assistant.\n\n"
        "STRUCTURE\n"
        "Use short paragraphs. Each paragraph should explain one idea. "
        "Depth should come from additional short paragraphs, not longer sentences. "
        "Most expansions will include 4-6 short paragraphs. "
        "No section headings. No bullet points. Just short paragraphs.\n\n"
        "CONTENT GUIDELINES\n"
        "Focus on explaining how the program actually works. "
        "Helpful topics often include:\n"
        "- Department structure\n"
        "- Cross-registration options\n"
        "- Undergraduate research access\n"
        "- Program pathways (for example 3-2 engineering or interdisciplinary tracks)\n"
        "- Internship pipelines\n"
        "- Career directions or graduate study trends\n"
        "- Practical realities students should know\n"
        "Avoid repeating information already stated in the Deep Dive summary.\n\n"
        "STYLE RULES\n"
        "- Write clearly and avoid long academic sentences.\n"
        "- No em dashes anywhere.\n"
        "- Do not address the reader directly.\n"
        "- Do not over-personalize using hobbies or profile details.\n"
        "- Avoid marketing phrases such as 'renowned program' or 'world-class faculty'.\n"
        "- Avoid unverifiable claims about specific employers recruiting from the school.\n"
        "- Avoid listing elite graduate schools unless it is widely documented.\n\n"
        "QUALITY CHECK\n"
        "If the writing becomes dense, academic, or generic, rewrite it so it is clearer, "
        "more natural, and easier to read."
    )

    user_prompt = (
        f"Write the 'More about this program' expansion for the {major} program at {school_name}.\n\n"
        f"School: {school_name}\n"
        + (f"Location: {location}\n" if location else "")
        + (f"School character: {vibe}\n" if vibe else "")
        + "\nWrite 4-6 short paragraphs. Each paragraph covers one idea about how the program "
        "actually works at this specific school. Focus on structure, research access, "
        "distinctive pathways, and practical realities. "
        "No headings. No bullet points. No em dashes. No marketing language. "
        "Do not address the reader directly."
    )

    try:
        resp = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=900,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        body = resp.content[0].text.strip()
        return jsonify({'body': body})
    except Exception as e:
        return jsonify({'error': str(e)}), 200


def _inject_cached_sections(sections, school_cached, program_cached,
                             school_name, major, minor, is_oou=False):
    """Insert or replace cached pre-generated sections in the parsed sections list.

    Handles two cases:
      REPLACE — Claude generated a section but cache has better content (safety net).
      INSERT  — Speed fix omitted section instructions from the prompt so Claude
                skipped them; inject inserts cached content at the correct position.

    Insert anchors:
      Known For + Academic: after 'Coach Interest' (in-universe) or after bottom_line (OOU)
      Campus Life + More:   after 'What It Costs'
    """
    if not school_cached and not program_cached:
        return sections

    def _split_hb(text):
        if not text:
            return '', ''
        parts = text.split('\n', 1)
        return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else '')

    # Pre-build cached section objects
    c_kf   = school_cached.get('known_for', '')
    c_cm   = school_cached.get('campus_life_main', '')
    c_cmor = school_cached.get('campus_life_more', '')
    c_am   = program_cached.get('academic_program_main', '')
    c_amor = program_cached.get('academic_program_more', '')
    c_min  = program_cached.get('minor_content', '')

    def _sec_known_for():
        return {'title': f"What {school_name} Is Known For", 'body': c_kf, 'type': 'content'}

    def _sec_academic():
        t, b = _split_hb(c_am)
        return {'title': t or 'Academic Program', 'body': b or c_am, 'type': 'academic_program'}

    def _sec_academic_more():
        _, b = _split_hb(c_amor)
        return {'title': 'More: Going Deeper', 'body': b or c_amor, 'type': 'more_academic'}

    def _sec_campus():
        return {'title': 'Campus Life', 'body': c_cm, 'type': 'student_experience'}

    def _sec_campus_more():
        _, b = _split_hb(c_cmor)
        return {'title': 'More: Life Outside the Pool', 'body': b or c_cmor, 'type': 'more_student_experience'}

    result_sections = []
    done = set()  # prevents duplicate insertions

    def _insert_known_for_and_academic():
        if c_kf and 'kf' not in done:
            result_sections.append(_sec_known_for())
            done.add('kf')
        if c_am and 'am' not in done:
            result_sections.append(_sec_academic())
            done.add('am')
            if c_amor:
                result_sections.append(_sec_academic_more())
                done.add('amor')

    def _insert_campus():
        if c_cm and 'cm' not in done:
            result_sections.append(_sec_campus())
            done.add('cm')
        if c_cmor and 'cmor' not in done:
            result_sections.append(_sec_campus_more())
            done.add('cmor')

    for section in sections:
        stype     = section.get('type', 'content')
        title_low = section.get('title', '').lower()

        # REPLACE: Known For
        if 'is known for' in title_low:
            if c_kf and 'kf' not in done:
                result_sections.append(_sec_known_for())
                done.add('kf')
                if c_am and 'am' not in done:
                    result_sections.append(_sec_academic())
                    done.add('am')
                    if c_amor:
                        result_sections.append(_sec_academic_more())
                        done.add('amor')
            else:
                result_sections.append(section)
            continue

        # REPLACE: Academic Program main
        if stype == 'academic_program':
            if c_am and 'am' not in done:
                result_sections.append(_sec_academic())
                done.add('am')
                if c_amor:
                    result_sections.append(_sec_academic_more())
                    done.add('amor')
            elif 'am' not in done:
                result_sections.append(section)
            continue

        # REPLACE: More: Going Deeper
        if stype == 'more_academic':
            if c_amor and 'amor' not in done:
                result_sections.append(_sec_academic_more())
                done.add('amor')
            elif 'amor' not in done:
                result_sections.append(section)
            continue

        # REPLACE: Campus Life
        if stype == 'student_experience':
            if c_cm and 'cm' not in done:
                result_sections.append(_sec_campus())
                done.add('cm')
            else:
                result_sections.append(section)
            continue

        # REPLACE: More: Student Experience / Life Outside the Pool
        if stype == 'more_student_experience':
            if c_cmor and 'cmor' not in done:
                result_sections.append(_sec_campus_more())
                done.add('cmor')
            else:
                result_sections.append(section)
            continue

        result_sections.append(section)

        # INSERT anchors — for speed fix where Claude skipped cached sections

        # In-universe: insert Known For + Academic after Coach Interest
        if 'coach interest' in title_low:
            _insert_known_for_and_academic()

        # OOU: insert Known For + Academic after bottom_line (no Coach Interest)
        if is_oou and stype == 'bottom_line':
            _insert_known_for_and_academic()

        # Both paths: insert Campus Life + More after What It Costs
        if 'what it costs' in title_low:
            _insert_campus()

    # Minor — always appended at end, never generated live
    if minor and c_min and 'minor' not in done:
        t, b = _split_hb(c_min)
        result_sections.append({
            'title': t or f'Minor in {minor}',
            'body':  b or c_min,
            'type':  'minor',
        })

    return result_sections


def _build_cost_instruction(school_name, school_cached, sat, gpa,
                             sat25, sat75, gpa_mean, is_ivy, money_block):
    """Build the ## What It Costs section instruction.

    Uses Layer 1 cached cost data when available. Falls back to the existing
    money_block behavior when cache is empty.
    """
    coa = school_cached.get('coa') if school_cached else None
    if not coa:
        return (
            "## What It Costs\n"
            "Use EXACTLY the MONEY DATA figures above. Do not change the numbers. "
            "Cover COA, merit or no merit, net cost, aid philosophy. Practical family language.\n"
        )

    merit_offered = school_cached.get('merit_offered', False)
    merit_low     = school_cached.get('merit_range_low')
    merit_high    = school_cached.get('merit_range_high')
    merit_notes   = school_cached.get('merit_notes') or ''
    need_headline = school_cached.get('need_based_headline') or ''

    # Compute merit projection signal from swimmer academics vs school ranges
    merit_signal = 'unknown'
    if sat and sat75 and sat > sat75:
        merit_signal = 'above_75th'
    elif sat and sat25 and sat > sat25:
        merit_signal = 'above_median'
    elif sat and sat25:
        merit_signal = 'at_or_below_median'
    elif gpa and gpa_mean and gpa > gpa_mean:
        merit_signal = 'above_median'
    elif gpa and gpa_mean:
        merit_signal = 'at_or_below_median'

    lines = [
        "## What It Costs",
        "Follow the Cost Section voice rules in your system prompt.",
        "SCHOOL DATA (published facts — use as stated, do not alter):",
        f"  COA: ${coa:,} per year",
    ]

    if is_ivy:
        lines.append("  Merit: None. Ivy League. Need-based financial aid only.")
    elif not merit_offered:
        lines.append(f"  Merit: {school_name} does not offer merit scholarships to incoming freshmen.")
    else:
        range_str = ''
        if merit_low and merit_high:
            range_str = f"${merit_low:,} to ${merit_high:,} per year"
        elif merit_high:
            range_str = f"up to ${merit_high:,} per year"
        lines.append(f"  Merit offered: Yes. Published range: {range_str}. {merit_notes}".rstrip())

        if merit_low and merit_high:
            if merit_signal == 'above_75th':
                proj_low  = merit_low + int((merit_high - merit_low) * 0.55)
                proj_high = merit_high
                lines.append(
                    f"SWIMMER MERIT PROJECTION: Swimmer academics sit above this school's 75th percentile. "
                    f"Project merit in the upper range: *~${proj_low:,}-${proj_high:,}/year*. "
                    f"Show estimated net cost (COA minus projected merit). Mark as estimate."
                )
            elif merit_signal == 'above_median':
                proj_low  = merit_low + int((merit_high - merit_low) * 0.25)
                proj_high = merit_low + int((merit_high - merit_low) * 0.60)
                lines.append(
                    f"SWIMMER MERIT PROJECTION: Swimmer academics are above the school's middle 50%. "
                    f"Project merit in the mid range: *~${proj_low:,}-${proj_high:,}/year*. "
                    f"Show estimated net cost. Mark as estimate."
                )
            elif merit_signal == 'at_or_below_median':
                lines.append(
                    "SWIMMER MERIT PROJECTION: Swimmer academics are at or below the school's middle 50%. "
                    "Merit is possible but do not project a specific number. "
                    "State that merit exists at this school but cannot be estimated without more information."
                )
            else:
                lines.append(
                    "SWIMMER MERIT PROJECTION: Insufficient academic data to project merit. "
                    "State that merit is available but do not estimate a specific number."
                )

    if need_headline:
        lines.append(f"NEED-BASED NOTE (include only if applicable): {need_headline}")
    else:
        lines.append("NEED-BASED: No notable need-based policy. Skip it entirely.")

    lines.append(
        "Write only the section body. Start with the COA number. "
        "School facts in plain text. Projections marked with an asterisk. "
        "No m-dashes. No 'significant investment.' Short and real."
    )
    return '\n'.join(lines) + '\n'


def _slug_for_heading(title):
    """Map a Claude ## heading to a stable section slug used by SSE events and frontend slots."""
    t = title.lower().strip()
    if 'bottom line'              in t: return 'bottom_line'
    if 'in the pool'              in t: return 'in_the_pool'
    if 'coach interest'           in t: return 'coach_interest'
    if 'is known for'             in t: return 'known_for'
    if t == 'academic program'       : return 'academic_program'
    if 'are you admissible'       in t: return 'are_you_admissible'
    if 'what it costs'            in t: return 'what_it_costs'
    if t == 'campus life'            : return 'campus_life'
    if t == 'outcomes'               : return 'outcomes'
    if 'how it compares'          in t: return 'how_it_compares'
    if 'more: going deeper'       in t: return 'more_going_deeper'
    if 'more: academic'           in t: return 'more_going_deeper'
    if 'more: student experience' in t: return 'more_student_experience'
    if 'life outside the pool'    in t: return 'more_student_experience'
    if 'more: career paths'       in t: return 'more_career_paths'
    if 'minor'                    in t: return 'minor'
    return 'unknown'


def _parse_sections(raw_text):
    """Parse the raw deep dive text into structured sections."""
    def _classify_section(t):
        slug = _slug_for_heading(t)
        # Map new slugs back to legacy type names consumed by _inject_cached_sections
        # and the existing frontend fallback renderer.
        _slug_to_type = {
            'bottom_line':            'bottom_line',
            'in_the_pool':            'content',
            'coach_interest':         'content',
            'known_for':              'content',
            'academic_program':       'academic_program',
            'are_you_admissible':     'content',
            'what_it_costs':          'content',
            'campus_life':            'student_experience',
            'outcomes':               'outcomes',
            'how_it_compares':        'content',
            'more_going_deeper':      'more_academic',
            'more_student_experience':'more_student_experience',
            'more_career_paths':      'more_career_paths',
            'minor':                  'minor',
            'unknown':                'content',
        }
        return _slug_to_type.get(slug, 'content')

    parts  = re.split(r'^## ', raw_text, flags=re.MULTILINE)
    sections = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        lines = part.split('\n', 1)
        title = lines[0].strip()
        body  = lines[1].strip() if len(lines) > 1 else ''
        if title and not title.startswith('#') and not title.startswith('---'):
            sections.append({'title': title, 'body': body,
                             'type': _classify_section(title)})
    return sections


@deep_dive_bp.route('/api/deep-dive', methods=['POST'])
def deep_dive():
    """
    Generate the 8-section deep dive narrative for one school.

    Body: { school }   (school must match a key in score_all results)
    Response: { sections: [{title, body}] } or { error }
    """
    data     = request.json or {}
    school   = data.get('school', '').strip()
    prof_ovr = data.get('profile', {})

    if not school:
        return jsonify({'error': 'school is required'}), 400

    times = prof_ovr.get('times') or JAMES['times']
    _profile_sat = prof_ovr.get('sat')
    _profile_gpa = prof_ovr.get('gpa')
    sat   = int(_profile_sat   or JAMES['sat'])
    gpa   = float(_profile_gpa or JAMES['gpa'])
    _has_profile_sat = bool(_profile_sat)
    _has_profile_gpa = bool(_profile_gpa)
    swimmer_name = prof_ovr.get('name') or JAMES['name']
    math_sat         = prof_ovr.get('mathSat',          JAMES.get('mathSat', ''))
    sat_projected    = prof_ovr.get('satProjected',     JAMES.get('satProjected', ''))
    math_sat_proj    = prof_ovr.get('mathSatProjected', JAMES.get('mathSatProjected', ''))
    act_score        = prof_ovr.get('actScore',         JAMES.get('actScore', 0)) or 0
    ap_count         = prof_ovr.get('apCount',          JAMES.get('apCount',  0)) or 0
    grad_year        = prof_ovr.get('gradYear',         '2026')
    # Resolve name aliases (e.g. "UPenn" → "University of Pennsylvania")
    _alias = SCHOOL_NAME_ALIASES.get(school.lower().strip())
    if _alias:
        school = _alias

    # Use pre-scored card data from frontend when available — skips full universe rebuild.
    # Fall back to build_school_universe() if cardResult is absent or missing required fields.
    _card = data.get('cardResult') or {}
    _card_valid = (
        isinstance(_card, dict)
        and _card.get('school')
        and 'adjTier' in _card
        and isinstance(_card.get('top3'), list)
        and isinstance(_card.get('meta'), dict)
    )

    is_oou = False
    if _card_valid:
        result = _card
        is_oou = bool(_card.get('outOfUniverse'))
    else:
        # ONE unified pool — all ~324 schools through the same builder
        all_results = build_school_universe(times, sat, gpa)
        result = next((r for r in all_results if r['school'] == school), None)

    if result is None:
        # School not in the D3 universe — check out-of-universe well-known schools
        oou_meta_found = _oou_lookup(school)
        if oou_meta_found:
            oou_adm = _oou_admission(oou_meta_found, sat, gpa)
            result = {
                'school':         school,
                'conference':     '',
                'division':       '',
                'adjTier':        '',
                'psf':            1.0,
                'admission':      oou_adm,
                'top3':           [],
                'hasDepth':       False,
                'allEvents':      [],
                'meta':           oou_meta_found,
                'confTierShort':  '',
                'confTier':       '',
                'confFinish2026': None,
                'confScore2026':  None,
                'confPowerClass': '',
                'hasSwimData':    False,
                'outOfUniverse':  True,
            }
            is_oou = True
        else:
            return jsonify({'error': f'School "{school}" not found'}), 404

    client = _get_anthropic()
    if not client:
        return jsonify({
            'error': 'AI deep dive is not configured',
            'detail': 'ANTHROPIC_API_KEY is missing or invalid',
        }), 503

    meta = result['meta']
    top3_text  = _build_top3_text(result['top3'])
    vibe_answers = data.get('vibeAnswers') or prof_ovr.get('vibe') or {}
    other_prefs  = data.get('otherPrefs', '')
    vibe_lines   = _build_vibe_lines(vibe_answers, other_prefs)

    _merit_sat = (sat if _has_profile_sat else 0) or (_act_to_sat(act_score) if act_score else 0)
    money = _estimate_merit_block(
        merit_level = meta.get('merit', 'moderate'),
        sat         = _merit_sat,
        gpa         = gpa,
        sat_median  = meta.get('satMedian', 0),
        accept      = meta.get('accept', 50),
    )
    money_block = (
        f"MONEY DATA — use these exact figures, do not invent different numbers:\n"
        f"Estimated COA: {money['coa']}\n"
        f"Estimated Merit: {money['merit']}"
        + (" (based on your academics)" if money['has_merit'] else "") + "\n"
        f"Estimated Net: {money['net']}\n"
        f"Merit note: {money['note']}"
    )

    vibe_block = ''
    if vibe_lines:
        vibe_block = (
            f"\n{swimmer_name.upper()}'S PERSONALITY & PREFERENCES "
            f"(use these to personalize Campus Life and tone):\n{vibe_lines}\n"
        )

    hidden_ivy_note = '\nThis school is academically elite and employer-respected without the Stanford-level rejection rate. Convey that reality without using the phrase "Hidden Ivy."' if meta.get('hiddenIvy') else ''
    stem_note       = '\nStrong STEM programs.' if meta.get('stem') else ''

    sat_detail = f"SAT {sat}" if (sat and _has_profile_sat) else ""
    if sat and _has_profile_sat and math_sat:
        sat_detail += f" (math {math_sat})"
    if act_score:
        sat_detail += (", " if sat_detail else "") + f"ACT {act_score}"
    ap_detail = f", {ap_count} projected APs" if ap_count else ""

    # Academic credentials string for prompts — only includes fields actually provided
    _acad_parts = []
    if _has_profile_gpa:
        _acad_parts.append(f"GPA {gpa} unweighted")
    if sat_detail:
        _acad_parts.append(sat_detail)
    if ap_detail.strip(', '):
        _acad_parts.append(ap_detail.strip(', '))
    _swimmer_acad_str = (", " + ", ".join(_acad_parts)) if _acad_parts else ""

    # Structured major inputs (take priority over vibe career/academic fallback)
    primary_major   = (prof_ovr.get('primaryMajor')   or data.get('primaryMajor',   '')).strip()
    minor = (prof_ovr.get('minor') or data.get('minor', '')).strip()

    # Determine academic direction for optional section.
    # Source of truth: primaryMajor (structured picker), minor field.
    # Fallback: academicGoal from vibe (vibe.academic). Never inferred from career vibe.
    if primary_major:
        academic_direction = primary_major
    else:
        academic_raw = (vibe_answers.get('academic') or '').strip()
        _generic = academic_raw in ('', 'Genuinely want to be well-rounded')
        academic_direction = academic_raw if not _generic else None

    # Admission comparison block
    sat_median   = meta.get('satMedian', 0)
    sat25        = meta.get('sat25', 0)
    sat75        = meta.get('sat75', 0)
    gpa_mean     = meta.get('gpaMean', 0)
    accept_rate  = meta.get('accept', 0)
    adm_parts = []
    if _has_profile_gpa:
        adm_parts.append(f"GPA {gpa} unweighted")
    if _has_profile_sat and sat:
        adm_parts.append(f"SAT {sat}")
    if act_score:
        adm_parts.append(f"ACT {act_score}")
    adm_swimmer = ", ".join(adm_parts) if adm_parts else "academics not provided"
    adm_school_parts = [f"~{accept_rate}% acceptance rate"]
    if sat_median:
        adm_school_parts.append(f"SAT median ~{sat_median}")
    if sat25 and sat75:
        adm_school_parts.append(f"SAT range ~{sat25}-{sat75}")
    if gpa_mean:
        adm_school_parts.append(f"GPA average ~{gpa_mean}")
    admission_comparison = (
        f"ADMISSION COMPARISON (use to write 'Are You Admissible?' — do not invent different numbers):\n"
        f"Swimmer: {adm_swimmer}\n"
        f"School: {', '.join(adm_school_parts)}\n"
        f"Admission outlook: {result['admission']['label']}"
    )

    prog_strength = _program_strength_desc(result)
    conf_tier_short = result.get('confTierShort', '')
    super_powerhouse_note = (
        f"\nIMPORTANT: {result['school']} dominates {result['conference']} and recruits well "
        "above what most peer schools in that conference can attract. "
        "In 'In the Pool', call this out directly and tell the swimmer to look closely "
        "at the current roster and committed recruits before assuming a spot. "
        "Do not use the phrase 'Super Powerhouse.'"
    ) if conf_tier_short == '1A' else ''

    # Load prompts fresh from disk on every request — never cached at module load.
    _deep_dive_prompt   = _load_prompt('lane4_deep_dive_prompt.txt')
    _bottom_line_prompt = _load_prompt('lane4_bottom_line_v2_prompt.txt')
    _cost_prompt        = _load_prompt('cost_prompt.txt')
    print(f'[deep_dive] prompts loaded: deep_dive={len(_deep_dive_prompt)}c '
          f'bottom_line={len(_bottom_line_prompt)}c cost={len(_cost_prompt)}c')

    _base_system = (
        _deep_dive_prompt + "\n\n"
        "Lane4 Output Rules (always apply):\n"
        "- Never use internal tier labels in output text: no 'Super Powerhouse', 'Powerhouse', "
        "'Hidden Ivy', 'Priority Recruit', 'Serious Recruit', 'Recruitable', or any recruiting "
        "likelihood label. Describe programs and fit in natural language instead.\n"
        "- Never use the word 'tier'.\n"
        "- Never use the words 'profile', 'good school', 'strong fit', or 'also'.\n"
        "- No em dashes anywhere in the output.\n"
        "- Respond using markdown sections starting with ## for each section title.\n"
        "- 2-3 sentences per section. Short paragraphs. Strong declarative sentences."
    )
    system_prompt = _base_system + "\n\n" + _bottom_line_prompt + "\n\n" + _cost_prompt

    ivy_note = '\nThis is an Ivy League school — need-based aid only, no merit scholarships.' if meta.get('ivyLeague') else ''

    # Build optional academic section instruction
    if academic_direction:
        _school_nm = result['school']
        _minor_note = f" The student is also pursuing a minor in {minor}." if minor else ""
        acad_section_instr = (
            "## Academic Program\n"
            "Use EXACTLY this heading: 'Academic Program'\n"
            f"Major: {academic_direction} at {_school_nm}.{_minor_note} "
            f"This is the highest-priority section when a major is known. 4-5 sentences. Be specific.\n"
            f"Cover: the exact department or program name at {_school_nm}; whether it sits in "
            f"engineering, arts and sciences, a dedicated college, or another structure; "
            f"how established or respected the program is; undergraduate research or lab access; "
            f"practical vs theoretical tilt; faculty accessibility; and what makes it distinctive at "
            f"{_school_nm} specifically. Include employer or grad school outcomes where relevant. "
            "Do not write generic 'strong academics' language. Sound informed and specific.\n\n"
        )
    else:
        acad_section_instr = (
            "[SKIP the academic section entirely. No major has been provided. "
            "Do not include an academic program section.]\n"
        )

    # Student Experience "More" section — always included
    _more_student_exp = (
        "\n## More: Student Experience\n"
        "Use EXACTLY this heading: 'More: Student Experience'\n"
        "Expanded student life section (shown behind a 'More about student life' button). 4-6 sentences:\n"
        "- Academic pressure level and pacing at this specific school\n"
        "- Collaboration vs competition in the academic culture\n"
        "- What students actually do outside of class and team\n"
        "- Social life anchors (campus, city, team, greek life, etc.)\n"
        "- What students commonly praise and what they commonly complain about\n"
        "No direct callbacks to stated preferences. No overpersonalization.\n"
    )

    # Outcomes + Career Paths — always included
    _outcomes_section = (
        "\n## Outcomes\n"
        "Your job: Explain what happens after graduation, specifically to this school's graduates.\n"
        "1. Named employers, not generic: 'McKinsey, Deloitte, Goldman' not 'consulting firms.'\n"
        "2. Named graduate schools: 'Johns Hopkins Medical, Penn Medical, UCSF' not 'top medical schools.'\n"
        "3. Career paths: Where do graduates in the swimmer's likely field actually go? Name specific paths.\n"
        "4. Geographic limits: Be honest — 'Alumni network strongest in Northeast, thin on West Coast' if true.\n"
        "5. One unexpected path per school: health policy, startup, nonprofit, government, etc.\n"
        "6. Graduate school pipelines: If strong, name them. If thinner, be honest.\n"
        "7. Do not oversell the network. Be honest about where it is strong and where it is weak.\n"
        "\n## More: Career Paths\n"
        "Use EXACTLY this heading: 'More: Career Paths'\n"
        "Expanded career section (shown behind a 'More about career paths' button). 6-8 sentences:\n"
        "- Typical employers by name (not just 'finance' but specific firms)\n"
        "- Graduate school pipelines: where graduates apply, acceptance rates if known\n"
        "- Industry concentrations this school is known for placing into\n"
        "- Geographic career advantages: does location or alumni base help in specific cities?\n"
        "- Alumni network strength and how alumni actually engage with undergraduates\n"
        "- On-campus recruiting, employer partnerships, or career center strengths\n"
        "- Honest gaps: industries or regions where this school's network is thin\n"
        "Sound informed. Name specifics. Do not promote.\n"
    )

    # Bottom Line instruction — v2 (label-aware) or v1 (original)
    _adj_tier  = result.get('adjTier', '')
    _division  = result.get('division', '')
    _conf_name = result.get('conference', '')

    # Bottom Line v2 is always active — prompts loaded fresh above enforce the voice standard.
    _bl_oou_instruction = (
        "## Bottom Line\n"
        "No swim data for this school. Focus on academic and personal fit.\n"
        "Follow the Bottom Line v2 voice standard in your system prompt.\n"
        "Structure: (1) Open with what makes the school elite, specific and earned. "
        "(2) Academic fit sentence: honest, specific, not generic praise. "
        "(3) What choosing this school means for swimming, honestly. "
        "(4) Closing line that lands. No filler.\n"
        "Roughly 3-5 sentences. No m-dashes. No money talk. No consolation-prize language.\n"
    )
    _bl_inuniverse_instruction = (
        "## Bottom Line\n"
        f"RECRUITING LIKELIHOOD: {_adj_tier} | DIVISION: {_division} | CONFERENCE: {_conf_name}\n"
        "Follow the Bottom Line v2 voice standard and tier voice matrix in your system prompt.\n"
        "Structure: (1) Open with what makes the school elite, specific and earned. "
        "(2) Pool sentence(s): meat not raw data, voice register matches the recruiting likelihood label above exactly. "
        "(3) Academic fit sentence. "
        "(4) Closing line that lands. No filler.\n"
        "Roughly 3-5 sentences. No m-dashes. No money talk. No consolation-prize language.\n"
    )

    # user_prompt is built inside the generator after pregen cache flags are known

    def _stream_deep_dive_response():
        """Generator function that streams deep dive chunks and sends final parsed result."""
        try:
            # Send keepalive comment to confirm connection is live before Claude starts generating
            yield ": keepalive\n\n"

            # Read cache only — never blocks. On a hit this is a single DB query.
            # On a miss, fire a background thread to generate and cache for next visit.
            # The Claude stream starts immediately either way.
            _school_cached  = {}
            _program_cached = {}
            try:
                _school_cached  = read_school_cache(result['school'])
                _program_cached = read_program_cache(
                    result['school'], academic_direction, minor or ''
                ) if academic_direction else {}
            except Exception as _pregen_err:
                print(f'[deep_dive] cache read error: {_pregen_err}')

            # Background generation on cache miss — does not block the stream.
            if not _school_cached:
                threading.Thread(
                    target=get_or_generate_school_content,
                    kwargs=dict(
                        school_name=result['school'],
                        division=result.get('division', ''),
                        conference=result.get('conference', ''),
                        region=meta.get('location', ''),
                        school_type=meta.get('type', ''),
                        meta={k: meta.get(k, '') for k in (
                            'freshmanHousing', 'greekLife', 'residentialPattern',
                            'athleteIntegration', 'genderRatio', 'townVibe',
                            'campusTemperature',
                        )},
                    ),
                    daemon=True,
                    name=f'pregen-school-{result["school"]}',
                ).start()
                print(f'[deep_dive] background pregen started: school={result["school"]}')

            if academic_direction and not _program_cached:
                threading.Thread(
                    target=get_or_generate_program_content,
                    kwargs=dict(
                        school_name=result['school'],
                        major=academic_direction,
                        minor=minor or '',
                        division=result.get('division', ''),
                    ),
                    daemon=True,
                    name=f'pregen-program-{result["school"]}-{academic_direction}',
                ).start()
                print(f'[deep_dive] background pregen started: program={academic_direction} at {result["school"]}')

            # Speed fix: omit section instructions for content already in cache.
            # _inject_cached_sections will INSERT those sections at the right position.
            _has_known_for   = bool(_school_cached.get('known_for'))
            _has_campus_life = bool(_school_cached.get('campus_life_main'))
            _has_academic    = bool(_program_cached.get('academic_program_main'))
            _has_cost        = bool(_school_cached.get('coa'))

            _kf_instr = (
                '' if _has_known_for else
                f"## What {result['school']} Is Known For\n"
                "School identity. Make it feel important and real. Prestige and seriousness when deserved. 3-4 sentences.\n"
            )
            _acad_instr  = '' if _has_academic else acad_section_instr
            _cost_instr  = _build_cost_instruction(
                result['school'], _school_cached, sat, gpa,
                sat25, sat75, gpa_mean, bool(meta.get('ivyLeague')), money_block,
            )
            # Include money_block preamble only when cost section falls back to it
            _money_preamble = '' if _has_cost else f"{money_block}\n"

            if is_oou:
                _campus_instr = (
                    '' if _has_campus_life else
                    "## Campus Life\n"
                    "What do four years here actually feel like? Size, energy, setting, social scene. 3-4 sentences.\n"
                    + _more_student_exp
                )
                user_prompt = (
                    f"Write a deep dive for {swimmer_name} considering {result['school']}.\n\n"
                    f"SWIMMER: {swimmer_name}, Class of {grad_year}{_swimmer_acad_str}."
                    f"{vibe_block}\n"
                    f"SCHOOL: {result['school']}\n"
                    f"School vibe: {meta.get('vibe', '')}\n"
                    f"Location: {meta.get('location', '')}\n"
                    f"{admission_comparison}\n"
                    f"{_money_preamble}"
                    f"{hidden_ivy_note}{ivy_note}{stem_note}\n\n"
                    "NOTE: This school is not in our swim recruiting database. The swimmer is comparing it "
                    "against D3 options — be honest about what choosing this school means for swim.\n\n"
                    "Write exactly these sections in this order:\n\n"
                    f"{_bl_oou_instruction}"
                    f"{_kf_instr}"
                    f"{_acad_instr}"
                    "## Are You Admissible?\n"
                    "Your job: Be brutally honest about admissions odds.\n"
                    "1. Show the numbers: GPA vs. median, SAT vs. range, acceptance rate (exact %, no 'approximately'). "
                    "If academic data was not provided, state that and focus on acceptance rate context.\n"
                    "2. Call it clearly: If acceptance rate is under 10%, say 'this is a reach.'\n"
                    "3. If SAT is notably below median AND acceptance is under 15%, say: "
                    "'This is a reach by any honest reading.'\n"
                    "4. Coach context: Mention coach support only if relevant. Do NOT oversell.\n"
                    "5. Do NOT use euphemism: 'competitive,' 'ambitious,' 'stretch' — use reach/realistic/safety.\n"
                    "6. If the numbers do not support admission, say so plainly.\n"
                    f"{_cost_instr}"
                    f"{_campus_instr}"
                    "## How It Compares to Your D3 Options\n"
                    "Be honest — what does choosing this school mean for continuing to swim competitively?\n"
                    f"{_outcomes_section}"
                )
            else:
                _campus_instr = (
                    '' if _has_campus_life else
                    "## Campus Life\n"
                    "What do four years here actually feel like? Size, energy, setting, social scene, "
                    "what kind of student thrives. No brochure copy. 3-4 sentences.\n"
                    + _more_student_exp
                )
                user_prompt = (
                    f"Write a deep dive for {swimmer_name} considering {result['school']}.\n\n"
                    f"SWIMMER: {swimmer_name}, Class of {grad_year}{_swimmer_acad_str}."
                    f"{vibe_block}\n"
                    f"SWIM DATA AT {result['school'].upper()} ({result['conference']}):\n"
                    f"Top events: {top3_text}\n"
                    f"Program strength: {prog_strength}\n"
                    f"{super_powerhouse_note}\n"
                    f"School vibe: {meta.get('vibe', '')}\n"
                    f"Location: {meta.get('location', '')}\n"
                    f"{admission_comparison}\n"
                    f"{_money_preamble}"
                    f"{hidden_ivy_note}{ivy_note}{stem_note}\n\n"
                    "Write exactly these sections in this order. "
                    "Swim fit is explained ONCE in 'In the Pool' — do not repeat it elsewhere. "
                    "Use the free response lightly and naturally — no overpersonalization. "
                    "Use 'Hidden Ivy' naturally if applicable.\n\n"
                    f"{_bl_inuniverse_instruction}"
                    "## In the Pool\n"
                    "Your job: Explain pool fit honestly, no hedging.\n"
                    "1. Conference level: What conference is this, and where do the swimmer's times sit?\n"
                    "2. Event reality: Is this an A final? B final? Contributing piece? "
                    "State actual conference placement. No vague projections.\n"
                    "3. Roster check: Direct them to verify depth at their events. "
                    "Do NOT claim to know what is on the current roster.\n"
                    "4. Time drop language: ONLY mention time improvement if the swimmer is BORDERLINE or REACH. "
                    "If they are a solid recruit at this program, do NOT say 'a drop makes it better' — they already belong.\n"
                    "5. Be specific to THIS school and THIS conference. Do not repeat generic language across schools.\n"
                    "## Coach Interest — What to Expect\n"
                    "Your job: Explain what this coach will actually do, honestly.\n"
                    "1. Coach profile: What do coaches at this program want? What is their recruiting style?\n"
                    "2. Academic check: One sentence maximum. If academics clear admission, say so and move on. "
                    "If no academic data was provided, skip this entirely.\n"
                    "3. Swimming reality: Will this coach move fast or be patient? What triggers urgency?\n"
                    "4. Time drop: ONLY mention trajectory if the swimmer is currently on the fence at this school. "
                    "Do not mention it if they are already a solid recruit.\n"
                    "5. Concrete action: Give a specific next step — 'Email the coach with your times' or 'Reach out before [season].'\n"
                    "6. Do not repeat 'course rigor matters' or generic academic language across outputs.\n"
                    f"{_kf_instr}"
                    f"{_acad_instr}"
                    "## Are You Admissible?\n"
                    "Your job: Be brutally honest about admissions odds.\n"
                    "1. Show the numbers: GPA vs. median, SAT vs. range, acceptance rate (exact %, no 'approximately'). "
                    "If academic data was not provided, state that and focus on acceptance rate context.\n"
                    "2. Call it clearly: If acceptance rate is under 10%, say 'this is a reach.'\n"
                    "3. If SAT is notably below median AND acceptance is under 15%, say: "
                    "'This is a reach by any honest reading.'\n"
                    "4. Coach context: Mention coach support only if relevant. Do NOT oversell at D3 level.\n"
                    "5. Do NOT use euphemism: 'competitive,' 'ambitious,' 'stretch' — use reach/realistic/safety.\n"
                    "6. If the numbers do not support admission, say so plainly.\n"
                    f"{_cost_instr}"
                    f"{_campus_instr}"
                    f"{_outcomes_section}"
                )

            full_text = ""
            print(f"[deep_dive] prompt word count: {len(user_prompt.split())}")

            # ── Emit cached sections immediately before Claude starts ─────────
            # Each cached blob is sent as a typed section event so the frontend
            # can populate its named slots without waiting for the Claude stream.
            def _split_hb(text):
                if not text:
                    return '', ''
                parts = text.split('\n', 1)
                return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else '')

            _cached_emit = []
            _school_name = result['school']

            if _school_cached.get('known_for'):
                _cached_emit.append({
                    'section': 'known_for',
                    'title':   f'What {_school_name} Is Known For',
                    'body':    _school_cached['known_for'],
                    'cached':  True,
                })
            if _program_cached.get('academic_program_main'):
                _t, _b = _split_hb(_program_cached['academic_program_main'])
                _cached_emit.append({
                    'section': 'academic_program',
                    'title':   _t or 'Academic Program',
                    'body':    _b or _program_cached['academic_program_main'],
                    'cached':  True,
                })
            if _program_cached.get('academic_program_more'):
                _, _b = _split_hb(_program_cached['academic_program_more'])
                _cached_emit.append({
                    'section': 'more_going_deeper',
                    'title':   'More: Going Deeper',
                    'body':    _b or _program_cached['academic_program_more'],
                    'cached':  True,
                })
            if _school_cached.get('campus_life_main'):
                _cached_emit.append({
                    'section': 'campus_life',
                    'title':   'Campus Life',
                    'body':    _school_cached['campus_life_main'],
                    'cached':  True,
                })
            if _school_cached.get('campus_life_more'):
                _, _b = _split_hb(_school_cached['campus_life_more'])
                _cached_emit.append({
                    'section': 'more_student_experience',
                    'title':   'More: Life Outside the Pool',
                    'body':    _b or _school_cached['campus_life_more'],
                    'cached':  True,
                })
            if minor:
                # Check program_content_cache.minor_content first, then minor_content_cache
                _minor_text = _program_cached.get('minor_content') or ''
                if not _minor_text:
                    _minor_text = _read_minor_cache(_school_name, minor)
                if _minor_text:
                    _t, _b = _split_hb(_minor_text)
                    _cached_emit.append({
                        'section': 'minor',
                        'title':   _t or f'Minor in {minor}',
                        'body':    _b or _minor_text,
                        'cached':  True,
                    })

            for _evt in _cached_emit:
                yield f"data: {json.dumps(_evt)}\n\n"

            # ── Heading-aware streaming loop ───────────────────────────────────
            # Detects ## heading boundaries in the raw chunk stream and emits
            # sectionStart / chunk / sectionEnd events so the frontend can stream
            # live sections into named slots.
            #
            # Buffer strategy: hold back any trailing text that could be the start
            # of a heading (ends with \n# or \n##) until the next chunk confirms
            # or refutes it.  Everything before that prefix is safe to emit.

            _cur_slug   = None   # slug of the section currently streaming
            _head_buf   = ''     # accumulates potential heading text
            # Matches a complete ## heading line anywhere in the buffer.
            # Group 1 = everything before the heading (content to flush as chunk).
            # Group 2 = heading title text.
            # Group 3 = everything after the heading newline (remainder).
            _HEAD_RE = re.compile(r'^(.*?)\n##\s+([^\n]+)\n(.*)$', re.DOTALL)
            # Also matches a heading at the very start of the buffer (no leading content).
            _HEAD_START_RE = re.compile(r'^##\s+([^\n]+)\n(.*)$', re.DOTALL)

            def _flush_heading_buf(buf):
                """Try to consume a complete ## heading from buf.
                Returns (pre_content, slug, title, remainder) if found,
                else (None, None, None, buf)."""
                m = _HEAD_START_RE.match(buf)
                if m:
                    title = m.group(1).strip()
                    return '', _slug_for_heading(title), title, m.group(2)
                m = _HEAD_RE.match(buf)
                if m:
                    title = m.group(2).strip()
                    return m.group(1), _slug_for_heading(title), title, m.group(3)
                return None, None, None, buf

            def _safe_prefix(text):
                """Return the longest prefix of text that contains no partial heading.
                Anything from the last \\n# onward is held back."""
                idx = len(text)
                for needle in ('\n##', '\n#'):
                    pos = text.rfind(needle)
                    if pos != -1:
                        idx = min(idx, pos)
                # Guard against a bare # at position 0
                if text.startswith('#'):
                    idx = 0
                return text[:idx], text[idx:]

            with client.messages.stream(
                model='claude-sonnet-4-6',
                max_tokens=3200,
                system=system_prompt,
                messages=[{'role': 'user', 'content': user_prompt}],
            ) as stream:
                for text_chunk in stream.text_stream:
                    full_text  += text_chunk
                    _head_buf  += text_chunk

                    # Consume all complete headings present in the buffer
                    while True:
                        pre, slug, title, _head_buf = _flush_heading_buf(_head_buf)
                        if slug is None:
                            break
                        # Flush any content before the heading into the current section
                        if pre:
                            evt = {'chunk': pre}
                            if _cur_slug:
                                evt['section'] = _cur_slug
                            yield f"data: {json.dumps(evt)}\n\n"
                        # Close previous section, open new one
                        if _cur_slug is not None:
                            yield f"data: {json.dumps({'sectionEnd': _cur_slug})}\n\n"
                        _cur_slug = slug
                        yield f"data: {json.dumps({'sectionStart': slug, 'title': title})}\n\n"

                    # Emit the safe prefix; hold back the potential partial heading
                    safe, _head_buf = _safe_prefix(_head_buf)
                    if safe:
                        evt = {'chunk': safe}
                        if _cur_slug:
                            evt['section'] = _cur_slug
                        yield f"data: {json.dumps(evt)}\n\n"

            # Flush whatever remains in the buffer after the stream ends
            while True:
                pre, slug, title, _head_buf = _flush_heading_buf(_head_buf)
                if slug is None:
                    break
                if pre:
                    evt = {'chunk': pre}
                    if _cur_slug:
                        evt['section'] = _cur_slug
                    yield f"data: {json.dumps(evt)}\n\n"
                if _cur_slug is not None:
                    yield f"data: {json.dumps({'sectionEnd': _cur_slug})}\n\n"
                _cur_slug = slug
                yield f"data: {json.dumps({'sectionStart': slug, 'title': title})}\n\n"
            if _head_buf:
                evt = {'chunk': _head_buf}
                if _cur_slug:
                    evt['section'] = _cur_slug
                yield f"data: {json.dumps(evt)}\n\n"

            if _cur_slug is not None:
                yield f"data: {json.dumps({'sectionEnd': _cur_slug})}\n\n"

            # Parse sections from complete text
            sections = _parse_sections(full_text)

            # Swap in cached pre-generated sections where available
            sections = _inject_cached_sections(
                sections, _school_cached, _program_cached,
                result['school'], academic_direction, minor,
                is_oou=is_oou,
            )

            if not sections:
                # Send error as final SSE event
                yield f"data: {json.dumps({'error': 'AI returned empty deep dive', 'done': True})}\n\n"
                return

            # Send final event with parsed sections
            final_event = {
                'done': True,
                'school': result['school'],
                'sections': sections,
                'admission': result['admission'],
                'adjTier': result['adjTier'],
                'meta': meta,
            }
            yield f"data: {json.dumps(final_event)}\n\n"

        except Exception as e:
            # Send error as final SSE event
            error_event = {
                'error': 'Deep dive failed',
                'detail': str(e),
                'done': True
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    return Response(
        stream_with_context(_stream_deep_dive_response()),
        mimetype='text/event-stream',
        headers={
            'X-Accel-Buffering': 'no',
            'Cache-Control': 'no-cache, no-transform',
        }
    )


@deep_dive_bp.route('/api/coach-email', methods=['POST'])
def coach_email():
    """
    Generate deterministic coach email for one school. No AI call.

    Body: { school, profile? }
    Response: { subject, body }
    """
    data     = request.json or {}
    school   = data.get('school', '').strip()
    prof_ovr = data.get('profile', {})

    if not school:
        return jsonify({'error': 'school is required'}), 400

    times         = prof_ovr.get('times') or JAMES['times']
    sat           = int(prof_ovr.get('sat')  or JAMES['sat'])
    gpa           = float(prof_ovr.get('gpa') or JAMES['gpa'])
    act_score     = prof_ovr.get('actScore', JAMES.get('actScore', 0)) or 0
    ap_count      = prof_ovr.get('apCount',  JAMES.get('apCount',  0)) or 0
    swimmer_name  = prof_ovr.get('name') or JAMES['name']
    grad_year     = prof_ovr.get('gradYear',     '2026')

    all_results = build_school_universe(times, sat, gpa)
    result = next((r for r in all_results if r['school'] == school), None)

    if result is None:
        return jsonify({'error': f'School "{school}" not found'}), 404

    meta  = result['meta']
    top3  = result['top3']
    best  = top3[0] if top3 else None

    # Performance descriptor
    if best is None:
        perf = 'still working toward being a scorer at your conference meet level'
        second = ''
        fit_sentence = (
            "I know I am not yet at a projected scoring level for your conference, "
            "but I am improving and very interested in your program.\n\n"
        )
    elif best['place'] <= 1.5:
        perf = f"projected to win the {best['event']}"
        second = f" I also project to score in the {top3[1]['event']}." if len(top3) > 1 else ''
        fit_sentence = ''
    elif best['place'] <= 3.5:
        perf = f"projected to podium in the {best['event']}"
        second = f" I also project to score in the {top3[1]['event']}." if len(top3) > 1 else ''
        fit_sentence = ''
    else:
        perf = f"projected as a conference A finalist in the {best['event']}"
        second = f" I also project to score in the {top3[1]['event']}." if len(top3) > 1 else ''
        fit_sentence = ''
    stem_note  = ' Your programs in engineering and CS align directly with my academic direction.' if meta.get('stem') else ''
    merit_note = " I've also been looking closely at your merit scholarship opportunities." if meta.get('merit') == 'high' else ''

    # Build times summary from actual swimmer times
    time_entries = list(times.items())
    if time_entries:
        times_text = ', '.join(f"{t} in the {e.lower()}" for e, t in time_entries[:3])
    else:
        times_text = 'competitive times across multiple events'

    # Determine grad year class label
    class_label = f"Class of {grad_year}"

    subject = f"Prospective Student-Athlete Inquiry — {class_label} | Competitive Swimmer"
    body = (
        f"Dear Coach,\n\n"
        f"My name is {swimmer_name} and I'm a student in the {class_label} with strong interest "
        f"in {result['school']}'s swim program.\n\n"
        f"At the {result['conference']} conference level, I'm {perf}.{second} "
        f"My current bests include {times_text}.\n\n"
        f"{fit_sentence}"
        f"Academically I carry a {gpa} GPA"
        + (f", {sat} SAT" if sat else "")
        + (f" / {act_score} ACT" if act_score else "")
        + (f", with {ap_count} APs projected" if ap_count else "")
        + f".{stem_note}{merit_note}\n\n"
        f"I'd love to connect about your program. Would you have time for a brief call or campus visit?\n\n"
        f"Thank you,\n{swimmer_name}"
    )

    return jsonify({'subject': subject, 'body': body})
