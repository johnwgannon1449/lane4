# Search prompt builders and response parsers extracted from main.py.
import re, json
from scoring.primitives import place_label

def _build_student_context(name, gpa, sat, act, times, vibe, other_prefs) -> str:
    """Build a concise student context string for GUIDED/CONSTRAINED prompts."""
    parts = [f"Student: {name or 'the swimmer'}"]
    if gpa:
        parts.append(f"GPA {gpa:.1f}")
    if sat:
        parts.append(f"SAT {sat}")
    elif act:
        parts.append(f"ACT {act}")
    if times:
        parts.append(f"events: {', '.join(list(times.keys())[:3])}")
    if vibe:
        skip = {'Not sure yet', 'Genuinely want to be well-rounded', '', None}
        prefs = [v for v in vibe.values() if v not in skip]
        if prefs:
            parts.append(f"preferences: {'; '.join(prefs[:4])}")
    if other_prefs and str(other_prefs).strip():
        parts.append(f"notes: {str(other_prefs).strip()[:200]}")
    return ', '.join(parts)


def _build_candidate_prompt(query: str, student_ctx: str) -> tuple:
    """Build system + user prompts for candidate school generation.

    Single path — always generates a strong pool of 12–15 relevant schools.
    Focus is purely on academic/program relevance to the query.
    Admissions and swim filtering are handled downstream, not here.
    Student context is always included so the LLM can interpret the query
    correctly (e.g. 'best bio schools for me'), but must NOT be used to
    pre-filter for fit.
    """
    system = (
        "You are an expert U.S. college counselor generating a candidate list of colleges.\n\n"
        "Rules — follow EXACTLY:\n"
        "- Focus on academic and program relevance to the query\n"
        "- Do NOT filter for admissions likelihood\n"
        "- Do NOT filter for swim/athletic fit\n"
        "- Do NOT rank for the student — just return a strong, relevant pool\n"
        "- Include a quality range — not just elite schools\n"
        "- Honor explicit constraints exactly (NESCAC, Midwest, D3, pre-med, STEM, etc.)\n"
        "- Return ONLY valid JSON — no markdown, no extra text\n"
        "- 'schools' must contain 12 to 15 full school name strings\n"
        "- 'answer' is 1-2 plain-English sentences describing the search\n"
        'Format: {"answer": "...", "schools": ["Full School Name", ...]}'
    )
    user_lines = [f'Search: "{query}"']
    if student_ctx:
        user_lines.append(
            f"\nStudent context (use only to understand the query — "
            f"do NOT pre-filter for admissibility or swim fit):\n{student_ctx}"
        )
    user_lines.append("\nReturn JSON only.")
    return system, '\n'.join(user_lines)


def _parse_candidate_names(text: str) -> tuple:
    """Parse LLM JSON → (answer str, list of school name strings)."""
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text).strip()
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        raise ValueError('No JSON in candidate response')
    parsed = json.loads(m.group())
    answer = str(parsed.get('answer', '')).strip()
    names  = [str(s).strip() for s in parsed.get('schools', []) if str(s).strip()]
    if not names:
        raise ValueError('Empty candidate list returned by AI')
    return answer, names





def _parse_search_response(text, sorted_35):
    """
    Parse Claude's JSON search response.
    Returns list of enriched SchoolResult dicts (with aiWhy), or raises ValueError.
    """
    # Strip markdown fences
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()

    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        raise ValueError('No JSON object found in response')

    parsed = json.loads(match.group())
    answer  = parsed.get('answer', '')
    picks   = parsed.get('schools', [])

    schools = []
    for pick in picks:
        idx = pick.get('number')
        if idx is None:
            continue
        idx = int(idx) - 1
        if idx < 0 or idx >= len(sorted_35):
            continue
        r = dict(sorted_35[idx])
        r['aiWhy'] = pick.get('why', '')
        schools.append(r)

    if not schools:
        raise ValueError('No valid school picks in response')

    return answer, schools

def _build_top3_text(top3):
    """'1650 Free: Contender; 500 Free: 🏅 Podium' style string."""
    return '; '.join(f"{e['event']}: {place_label(e['place'])}" for e in top3)

def _build_vibe_lines(vibe, other_prefs=''):
    """Format answered vibe questions for deep dive prompt."""
    labels = {
        'swimGoal': 'Swim environment goal',
        'campus':   'Ideal campus feel',
        'friday':   'Friday night preference',
        'academic': 'Academic priority',
        'compete':  'Competition mindset',
        'location': 'Location preference',
        'career':   'Career interest',
    }
    lines = []
    if vibe:
        for k, v in vibe.items():
            if v:
                lines.append(f"  - {labels.get(k, k)}: {v}")
    if other_prefs and other_prefs.strip():
        lines.append(f"  - Additional preferences: {other_prefs.strip()}")
    return '\n'.join(lines)
