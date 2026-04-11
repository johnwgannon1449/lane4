# School name resolution and fuzzy matching extracted from main.py.
import re
from models.school_aliases import _ACRONYM_ALIASES, _UNIVERSE_ALIASES, _US_STATES, _CAND_STOP

def _cname_norm(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', s.lower())).strip()


def _cname_toks(s: str) -> frozenset:
    return frozenset(t for t in _cname_norm(s).split()
                     if t not in _CAND_STOP and len(t) > 1)




# ── School-name query normalizer ──────────────────────────────────────────────
def _qnorm(s: str) -> str:
    """Normalize a user-typed query for liberal school-name matching.

    Handles: lowercase, punctuation strip, & → and, st → saint,
    trailing/leading whitespace, collapsed internal spaces.
    """
    s = s.lower().strip()
    s = s.replace('&', ' and ')
    s = re.sub(r"['\u2019`\-\.]", '', s)   # apostrophes, hyphens, periods
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    s = re.sub(r'\bst\b', 'saint', s)      # st → saint (before whitespace collapse)
    s = re.sub(r'\buniv\b', 'university', s)
    s = re.sub(r'\bcoll\b', 'college', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s





def _school_entity_surface(record: dict) -> str:
    """Build a normalized search surface for a school record.

    Combines canonical name + city + state abbreviation + full state name so
    that city-based queries ("Nashville", "Pittsburgh", "Santa Barbara") can
    match schools even when the city doesn't appear in the school's name.
    Conference is intentionally excluded — too broad, causes false positives.
    """
    parts = [_qnorm(record['school'])]
    meta  = record.get('meta') or {}
    loc   = meta.get('location', '')      # e.g. "Santa Barbara, CA"
    if loc:
        clean = re.sub(r'[,.]', ' ', loc)
        parts.append(_qnorm(clean))
        bits = [b.strip() for b in loc.split(',')]
        if len(bits) >= 2:
            state_ab   = bits[-1].strip().upper()
            full_state = _US_STATES.get(state_ab, '')
            if full_state:
                parts.append(_qnorm(full_state))
    return ' '.join(parts)


def _resolve_school_names(query: str, all_results: list) -> list:
    """School-entity resolver: find plausible universe matches for a user query.

    Six-pass pipeline (highest confidence first):
      1. Acronym alias    — pure initials / nickname contractions (CMU, WashU …)
      2. Exact normalized — _qnorm(query) == _qnorm(school_name)
      3. Name substring   — query contained in school name, or vice-versa
      4. Prefix-token     — every query token is a prefix of some school-name
                            token; catches truncated canonicals:
                              "Penn State"   → "Pennsylvania State University"
                              "Georgia Tech" → "Georgia Institute of Technolog"
      5. difflib fuzzy    — typos / near-misspellings (cutoff 0.60)
      6. Surface fallback — city / state match, ONLY when passes 1–5 return
                            nothing (e.g. "Nashville" → Vanderbilt University)

    Favors recall: returns all matches with confidence ≥ 0.55 so that
    ambiguous queries like "Washington" surface multiple schools for the user
    to pick from, rather than silently returning the first hit.
    """
    import difflib

    q_raw = query.strip()
    q_n   = _qnorm(q_raw)

    by_name    = {r['school']: r for r in all_results}
    canon_list = list(by_name.keys())
    scores: dict[str, float] = {}

    def _add(school: str, score: float) -> None:
        if school in by_name:
            scores[school] = max(scores.get(school, 0.0), score)

    # ── Pass 1: Acronym / nickname alias ──────────────────────────────────────
    alias_hit = _ACRONYM_ALIASES.get(q_n)
    if alias_hit:
        targets = alias_hit if isinstance(alias_hit, list) else [alias_hit]
        for t in targets:
            _add(t, 1.0)

    # ── Pass 2: Exact normalized name ─────────────────────────────────────────
    norm_to_canon = {_qnorm(n): n for n in canon_list}
    exact = norm_to_canon.get(q_n)
    if exact:
        _add(exact, 1.0)

    # ── Pass 3: Name substring (both directions, min 4 chars) ─────────────────
    if len(q_n) >= 4:
        for name in canon_list:
            s_n = _qnorm(name)
            if q_n in s_n:
                _add(name, 0.85)
            elif len(s_n) >= 4 and s_n in q_n:
                _add(name, 0.80)

    # ── Pass 4: Prefix-token match ────────────────────────────────────────────
    # Every query token must be a prefix of at least one school-name token.
    # Handles truncated canonical names and common abbreviations:
    #   "Penn State"   → tokens ["penn","state"] prefix-match ["pennsylvania","state",…]
    #   "Georgia Tech" → tokens ["georgia","tech"] prefix-match ["georgia","…technolog"]
    #   "Johns Hopkin" → ["johns","hopkin"] prefix-match ["johns","hopkins",…]
    _PREFIX_STOP = frozenset({'of', 'the', 'and', 'at', 'for', 'in', 'a'})
    q_toks = [t for t in q_n.split() if t not in _PREFIX_STOP and len(t) > 1]
    if len(q_toks) >= 2:
        for name in canon_list:
            s_toks = [t for t in _qnorm(name).split()
                      if t not in _PREFIX_STOP and len(t) > 1]
            if s_toks and all(any(st.startswith(qt) for st in s_toks)
                              for qt in q_toks):
                _add(name, 0.78)

    # ── Pass 5: difflib fuzzy (typos / close misspellings, cutoff 0.80) ───────
    norm_list = list(norm_to_canon.keys())
    for fuzzy_q, src, lookup in [
        (q_raw, canon_list, lambda h: h if h in by_name else None),
        (q_n,   norm_list,  lambda h: norm_to_canon.get(h)),
    ]:
        for hit in difflib.get_close_matches(fuzzy_q, src, n=6, cutoff=0.80):
            canon = lookup(hit)
            if canon:
                ratio = difflib.SequenceMatcher(None, q_n, _qnorm(canon)).ratio()
                _add(canon, ratio * 0.80)

    # ── Pass 6: City / state surface fallback ─────────────────────────────────
    # ONLY activated when passes 1–5 found nothing.
    # Allows city-based queries: "Nashville" → Vanderbilt, "Pittsburgh" → Pitt.
    # Does NOT fire when passes 1–5 already returned name-based matches, so
    # "Washington" stays clean (4 name matches) without adding every DC school.
    if not scores and len(q_n) >= 4:
        for record in all_results:
            if q_n in _school_entity_surface(record):
                _add(record['school'], 0.65)

    if not scores:
        return []

    # Confidence gate (0.55): drops weak difflib coincidences while keeping
    # alias hits (1.0), substring hits (0.80–0.85), prefix-token (0.78),
    # strong difflib (≥ 0.60 ratio × 0.80 = 0.48 … raise cutoff to 0.60 so
    # stored score ≥ 0.60 × 0.80 = 0.48 … hmm).
    # With cutoff=0.60, difflib stored score = ratio*0.80 ≥ 0.48 for a hit.
    # Alias/exact/substring/prefix are all ≥ 0.78, well above 0.55.
    MIN_CONF = 0.55
    return [by_name[name] for name, sc in
            sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
            if sc >= MIN_CONF]


def _map_to_universe(candidate_names: list, all_results: list) -> list:
    """
    Fuzzy-map LLM-generated school names → Lane4 school records.

    Matching priority:
      0. Alias map  — handles acronyms/short-forms (MIT, Caltech, NYU, etc.)
      1. Exact normalized match
      2. Substring match (normalized name contained in/containing each other)
      3. Key-token Jaccard similarity ≥ 0.50
    Ignores candidates that don't match confidently; never fabricates schools.
    """
    by_norm       = {_cname_norm(r['school']): r for r in all_results}
    by_canon_norm = {_cname_norm(r['school']): r for r in all_results}
    mapped, seen  = [], set()

    for cand in candidate_names:
        cand = cand.strip()
        if not cand:
            continue
        record = None

        # 0. Alias map — full official name → known short-form in universe
        alias_target = _UNIVERSE_ALIASES.get(_cname_norm(cand))
        if alias_target:
            record = by_canon_norm.get(_cname_norm(alias_target))

        # 1. Exact normalized match
        if not record:
            record = by_norm.get(_cname_norm(cand))

        # 2. Substring match
        if not record:
            c_n = _cname_norm(cand)
            for s_n, r in by_norm.items():
                if c_n and (c_n in s_n or s_n in c_n):
                    record = r
                    break

        # 3. Key-token Jaccard ≥ 0.50
        if not record:
            c_t = _cname_toks(cand)
            best, best_r = 0.0, None
            for r in all_results:
                r_t = _cname_toks(r['school'])
                if not c_t or not r_t:
                    continue
                jac = len(c_t & r_t) / len(c_t | r_t)
                if jac > best:
                    best, best_r = jac, r
            if best >= 0.50:
                record = best_r

        if record and record['school'] not in seen:
            seen.add(record['school'])
            mapped.append(record)

    return mapped


