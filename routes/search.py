"""routes/search.py — Natural-language school search endpoint."""
import json

from flask import Blueprint, request, jsonify
from ai_client import _get_anthropic
from models.swimmer_defaults import JAMES
from models.school_data import _oou_lookup
from scoring.admission import _oou_admission
from scoring.universe import build_school_universe
from search.intent import _detect_query_intent
from search.filters import (
    _ADM_LABEL_SCORE,
    _similarity_sort, _pre_sort, _program_strength_desc, _build_school_line,
    _hard_filter,
)
from search.school_resolver import _resolve_school_names, _map_to_universe
from search.prompts import (
    _build_student_context, _build_candidate_prompt,
    _parse_candidate_names, _parse_search_response,
)

search_bp = Blueprint('search', __name__)


@search_bp.route('/api/search', methods=['POST'])
def search():
    """
    Natural-language search: re-sort top-35 by query intent, call Claude,
    return 6 annotated SchoolResult objects.

    If the query exactly (or partially) matches a school name, that school is
    returned first as a directMatch, and Claude picks 5 related schools.
    Otherwise Claude picks 6 schools from the pre-sorted pool.

    Body: { query, eliminated?, myList? }
    Response: { answer, schools, directMatch? } or { error, fallback? }
    """
    data       = request.json or {}
    query      = data.get('query', '').strip()
    eliminated = data.get('eliminated', [])
    my_list    = data.get('myList', [])
    prof_ovr   = data.get('profile', {})

    if not query:
        return jsonify({'error': 'Query is required'}), 400

    times         = prof_ovr.get('times') or JAMES['times']
    sat           = int(prof_ovr.get('sat')  or JAMES['sat'])
    gpa           = float(prof_ovr.get('gpa') or JAMES['gpa'])
    act_score     = prof_ovr.get('actScore', JAMES.get('actScore', 0)) or 0
    ap_count      = prof_ovr.get('apCount',  JAMES.get('apCount',  0)) or 0
    swimmer_name  = prof_ovr.get('name') or JAMES['name']
    # ONE unified pool — all ~324 schools through the same builder
    all_results = build_school_universe(times, sat, gpa)

    # ── School-name resolution (alias + fuzzy, replaces old exact+substring) ────
    excl_names   = set(eliminated) | set(my_list)
    resolved     = _resolve_school_names(query, all_results)
    # Strip schools the user already eliminated or added to their list
    resolved     = [r for r in resolved if r['school'] not in excl_names]

    # Multi-match: ambiguous query matched 2+ schools → return them all, skip AI
    if len(resolved) >= 2:
        for r in resolved:
            adm_lbl  = (r.get('admission') or {}).get('label', '')
            swim_lbl = r.get('adjTier', '')
            parts    = [p for p in [swim_lbl, adm_lbl]
                        if p and p not in ('Unknown', '', 'Not Competitive', 'Below Roster Level')]
            r['aiWhy'] = ' · '.join(parts[:2])
        n = len(resolved)
        answer = (
            f'Found {n} schools matching "{query}" — which one did you mean?'
            if n <= 8 else
            f'Found {n} schools matching "{query}".'
        )
        return jsonify({
            'answer':      answer,
            'schools':     resolved,
            'directMatch': False,
            'multiMatch':  True,
        })

    direct_match = resolved[0] if resolved else None

    # Out-of-universe stub — ONLY for school names truly not in the 324.
    # This handles schools like Harvard, Stanford that are outside D3 swimming.
    if not direct_match:
        _desc_words = {
            'find','show','good','best','near','with','help','looking','want',
            'suggest','recommend','schools','colleges','programs','like','similar',
            'strong','competitive','academic','research','liberal','division','d3',
            'private','public','small','large','northeast','south','west','midwest',
            # Question / intent words — prevent "where should I swim" from being
            # treated as a school name stub
            'where','should','would','could','can','what','which','how','why',
            'when','swim','swimming','get','into','for','me','my','i',
        }
        _words = query.lower().split()
        _looks_like_name = (
            1 <= len(_words) <= 5 and
            not any(w in _desc_words for w in _words) and
            not query.lower().endswith('?')
        )
        if _looks_like_name:
            display_name = ' '.join(
                w.upper() if w == w.upper() and len(w) > 1 else w.title()
                for w in query.split()
            )
            oou_meta = _oou_lookup(display_name) or {}
            oou_adm  = (_oou_admission(oou_meta, sat, gpa)
                        if oou_meta else {'label': '', 'color': '#94A3B8', 'total': None})
            direct_match = {
                'school':         display_name,
                'conference':     '',
                'division':       '',
                'adjTier':        '',
                'psf':            1.0,
                'admission':      oou_adm,
                'top3':           [],
                'hasDepth':       False,
                'allEvents':      [],
                'meta':           oou_meta,
                'confTierShort':  '',
                'confTier':       '',
                'confFinish2026': None,
                'confScore2026':  None,
                'confPowerClass': '',
                'hasSwimData':    False,
                'outOfUniverse':  True,
            }

    if direct_match:
        excl_names.add(direct_match['school'])

    candidates = [r for r in all_results if r['school'] not in excl_names]
    if direct_match:
        # Sort by institutional similarity (division + selectivity + conf tier),
        # NOT by swimmer adjPts — so "similar schools" reflects what the school
        # is like, independently of how well this swimmer happens to score there.
        pool = _similarity_sort(candidates, direct_match)[:35]
    else:
        pool = candidates[:35]

    client = _get_anthropic()
    if not client:
        fallback = ([{**direct_match, 'directMatch': True}] if direct_match else []) + pool[:5 if direct_match else 6]
        return jsonify({
            'error': 'AI search is not configured',
            'detail': 'ANTHROPIC_API_KEY is missing or invalid',
            'fallback': fallback[:6],
            'directMatch': bool(direct_match),
        }), 503

    # ── NON-DIRECT: BROAD-POOL PATH or 4-STEP PIPELINE ──────────────────────
    if not direct_match:
        # ── BROAD-POOL PATH ──────────────────────────────────────────────────
        # Triggered for: prestige + personal + swim queries
        #   e.g. "most elite schools I can swim for"
        #        "every academic school I can contribute at"
        #        "all schools where I can make the team"
        #
        # Bypasses Claude's narrow 12–15 candidate generation.
        # Filters the full ~330-school universe to every swim-viable school,
        # sorts by academic selectivity (prestige first) then swim fit,
        # and returns the entire viable pool — typically 50–150 schools.
        _bp_intent = _detect_query_intent(query)
        _bp_intent['has_any_times'] = bool(times)

        _BROAD_EXPLICIT = (
            'all schools', 'every school', 'full list', 'complete list',
            'all the schools', 'every college', 'all colleges',
        )
        _is_broad_pool = (
            (_bp_intent['is_prestige_sort'] or
             any(t in query.lower() for t in _BROAD_EXPLICIT))
            and _bp_intent['is_personal']
            and _bp_intent['is_swim']
            and not _bp_intent['is_explicit_reach']
        )

        if _is_broad_pool:
            excl_set  = set(eliminated) | set(my_list)
            full_pool = [r for r in all_results if r['school'] not in excl_set]

            # Viable = has benchmark data AND swim tier above "Not Competitive".
            # "Below Roster Level" included as near-scoring / development range.
            viable = [
                r for r in full_pool
                if r.get('hasSwimData')
                and r.get('adjTier', '') not in ('', 'Not Competitive')
            ]

            # Primary sort: most selective first (accept% asc; unknown → 999).
            # Secondary: better swim fit wins within the same selectivity band.
            viable.sort(key=lambda r: (
                r['meta'].get('accept') or 999,
                -r.get('adjPts', 0),
            ))

            # Attach fit labels — same logic as main pipeline
            for r in viable:
                adm_lbl  = r.get('admission', {}).get('label', '')
                swim_lbl = r.get('adjTier', '')
                parts    = [p for p in [adm_lbl, swim_lbl] if p and p not in ('Unknown', '')]
                r['aiWhy'] = ' · '.join(parts[:2])

            n       = len(viable)
            top_str = ', '.join(r['school'] for r in viable[:5]) if viable else 'none found'

            # Tier breakdown for answer
            _tier_order = [
                'High-Point Contender', 'Conference Star', 'Top Recruit',
                'Priority Recruit', 'Recruitable', 'Below Roster Level',
            ]
            tier_counts = {t: 0 for t in _tier_order}
            for r in viable:
                t = r.get('adjTier', '')
                if t in tier_counts:
                    tier_counts[t] += 1
            tier_summary = ', '.join(
                f"{cnt} {t}" for t, cnt in tier_counts.items() if cnt > 0
            )

            answer = (
                f"Found {n} programs where you have a realistic shot to contribute, "
                f"ordered from most to least selective. "
                f"Leading the list: {top_str}. "
                f"Swim fit across the pool — {tier_summary}."
            )

            return jsonify({
                'answer':      answer,
                'schools':     viable,
                'directMatch': False,
                'broadPool':   True,
                '_debug': {
                    'intent':     _bp_intent,
                    'poolSize':   len(full_pool),
                    'viableSize': n,
                    'path':       'broad-pool-prestige-swim',
                },
            })

        # ── 4-STEP AI PIPELINE (unchanged for all other queries) ─────────────
        # Step 1 — AI generates candidate pool (~12–15 schools)
        # Step 2 — Hard filter using our truth labels
        # Step 3 — Sort by admissions fit / prestige
        # Step 4 — Return top 6 (or 12 for "show me more")
        vibe_answers  = data.get('vibeAnswers', {}) or {}
        other_prefs_s = data.get('otherPrefs', '') or ''

        # Always build student context — LLM uses it to interpret the query,
        # NOT to pre-filter for fit.
        student_ctx = _build_student_context(
            swimmer_name, gpa, sat, act_score, times, vibe_answers, other_prefs_s,
        )
        cand_sys, cand_usr = _build_candidate_prompt(query, student_ctx)

        try:
            # ── STEP 1: AI generates ~12–15 relevant schools ───────────────
            resp = client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=700,
                system=cand_sys,
                messages=[{'role': 'user', 'content': cand_usr}],
            )
            answer, candidate_names = _parse_candidate_names(resp.content[0].text)

            # Map LLM names → scored universe records (fuzzy match)
            excl_set   = set(eliminated) | set(my_list)
            avail      = [r for r in all_results if r['school'] not in excl_set]
            candidates = _map_to_universe(candidate_names, avail)

            # Fallback if mapping collapses entirely
            if len(candidates) < 3:
                fallback = _pre_sort(all_results, query, eliminated, my_list)[:6]
                return jsonify({
                    'error': 'Candidate mapping too narrow',
                    'fallback': fallback,
                    'directMatch': False,
                }), 200

            # ── STEP 2: Detect intent → objective or personal ───────────────
            intent = _detect_query_intent(query)
            intent['has_any_times'] = bool(times)   # False → no swim scoring possible
            want_more = any(w in query.lower() for w in (
                'more schools', 'more options', 'show me more', 'give me more',
            ))
            limit = 12 if want_more else 6

            if not intent['is_personal']:
                # ── OBJECTIVE query ("best STEM schools", "fastest teams") ──
                # Return Claude's list in its exact order. No Lane4 filtering,
                # no admissions re-sort. We only use our data for display.
                schools       = candidates[:limit]
                removed_debug = []
            else:
                # ── PERSONAL query ("fastest schools I can get into") ───────
                # Filter by admissions threshold and/or swim competitiveness.
                filtered, removed_debug = _hard_filter(candidates, intent)

                # Prestige / ceiling sort: re-rank survivors by academic
                # selectivity when the user asked for "hardest / strongest
                # academic / most selective / elite" options.
                # Ranking: lowest _ADM_LABEL_SCORE = hardest to get into = first.
                # Ties (same label) broken by adjPts descending — better swim
                # fit wins within the same academic difficulty band.
                if intent.get('is_prestige_sort') and filtered:
                    filtered = sorted(
                        filtered,
                        key=lambda r: (
                            _ADM_LABEL_SCORE.get(
                                r.get('admission', {}).get('label', 'Unknown'), 25
                            ),
                            -r.get('adjPts', 0),
                        ),
                    )

                schools = filtered[:limit]

            # Attach a brief fit label to each card (no extra AI call)
            for r in schools:
                adm_lbl  = r.get('admission', {}).get('label', '')
                swim_lbl = r.get('adjTier', '')
                parts    = [p for p in [adm_lbl, swim_lbl] if p and p not in ('Unknown', '')]
                r['aiWhy'] = ' · '.join(parts[:2])

            if not schools:
                fallback = _pre_sort(all_results, query, eliminated, my_list)[:6]
                for r in fallback:
                    adm_lbl  = r.get('admission', {}).get('label', '')
                    swim_lbl = r.get('adjTier', '')
                    parts    = [p for p in [adm_lbl, swim_lbl] if p and p not in ('Unknown', '')]
                    r['aiWhy'] = ' · '.join(parts[:2])
                return jsonify({
                    'answer': 'No exact matches survived the fit filters, so here are the closest alternatives from the full pool.',
                    'schools': fallback,
                    'directMatch': False,
                    'fallbackUsed': True,
                    '_debug': {
                        'intent':       intent,
                        'aiCandidates': candidate_names,
                        'mapped':       [r['school'] for r in candidates],
                        'removed':      removed_debug,
                        'kept':         [],
                        'finalTop6':    [r['school'] for r in fallback[:6]],
                    },
                })

            # ── DEBUG — visible in DevTools → Network tab ───────────────────
            survived_reason = (
                'objective query — AI order preserved, no filtering'
                if not intent['is_personal']
                else 'passed admissions/swim filters, AI order preserved'
            )
            debug_kept = [
                {
                    'school':      r['school'],
                    'admissions':  r.get('admission', {}).get('label', 'Unknown'),
                    'swimTier':    r.get('adjTier', 'n/a') or '(empty — no scorable events)',
                    'hasSwimData': r.get('hasSwimData', False),
                    'survived':    survived_reason,
                }
                for r in schools
            ]

            return jsonify({
                'answer':      answer,
                'schools':     schools,
                'directMatch': False,
                '_debug': {
                    'intent':       intent,
                    'aiCandidates': candidate_names,
                    'mapped':       [r['school'] for r in candidates],
                    'removed':      removed_debug,
                    'kept':         debug_kept,
                    'finalTop6':    [r['school'] for r in schools[:6]],
                },
            })

        except json.JSONDecodeError as e:
            fallback = _pre_sort(all_results, query, eliminated, my_list)[:6]
            return jsonify({
                'error': 'AI returned malformed JSON',
                'detail': str(e),
                'fallback': fallback,
                'directMatch': False,
            }), 200

        except (ValueError, Exception) as e:
            fallback = _pre_sort(all_results, query, eliminated, my_list)[:6]
            return jsonify({
                'error': 'Search failed',
                'detail': str(e),
                'fallback': fallback,
                'directMatch': False,
            }), 200

    # ── DIRECT MATCH PATH — unchanged (similarity sort + Claude picks 5) ─────
    system_prompt = (
        "You are Lane4. Respond ONLY with a valid JSON object. "
        "No markdown. No explanation. Start with { end with }. "
        "Keep 'why' fields under 15 words each. Keep 'answer' under 30 words."
    )

    school_lines = '\n'.join(_build_school_line(i, r) for i, r in enumerate(pool))
    user_prompt  = (
        f'The user searched for "{direct_match["school"]}" '
        f'({direct_match["conference"] or "independent"}, {direct_match.get("division","")}, '
        f'program strength: {_program_strength_desc(direct_match)}).\n\n'
        f"{swimmer_name}: GPA {gpa}, SAT {sat}" + (f", ACT {act_score}" if act_score else "") + ".\n\n"
        f"This list is already sorted by institutional similarity to {direct_match['school']} "
        f"(same division, selectivity, and conference tier). "
        "Pick the 5 schools that are most genuinely similar — consider academic culture, "
        "school size, mission, and athletic program level. Ignore the swimmer's times when judging similarity. "
        "Return ONLY JSON.\n\n"
        f"{school_lines}\n\n"
        'JSON format:\n{"answer":"1-2 sentences why these schools are similar to '
        f'{direct_match["school"]}' + '","schools":[{"number":1,"why":"under 15 words"}]}'
    )

    try:
        resp = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=800,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        raw_text = resp.content[0].text
        answer, ai_schools = _parse_search_response(raw_text, pool)

        _dm_conf  = direct_match.get('conference', '')
        _dm_div   = direct_match.get('division', '')
        _dm_str   = _program_strength_desc(direct_match)
        _dm_parts = [p for p in [_dm_conf, _dm_div, _dm_str] if p]
        _dm_why   = ' · '.join(_dm_parts) if _dm_parts else 'Matched from our database'
        dm        = {**direct_match, 'directMatch': True, 'aiWhy': _dm_why}
        schools   = [dm] + ai_schools

        return jsonify({'answer': answer, 'schools': schools, 'directMatch': True})

    except json.JSONDecodeError as e:
        fallback = [{**direct_match, 'directMatch': True}] + pool[:5]
        return jsonify({
            'error': 'AI returned malformed JSON',
            'detail': str(e),
            'fallback': fallback[:6],
            'directMatch': True,
        }), 200

    except ValueError as e:
        fallback = [{**direct_match, 'directMatch': True}] + pool[:5]
        return jsonify({'error': str(e), 'fallback': fallback[:6], 'directMatch': True}), 200

    except Exception as e:
        fallback = [{**direct_match, 'directMatch': True}] + pool[:5]
        return jsonify({
            'error': 'Search failed',
            'detail': str(e),
            'fallback': fallback[:6],
            'directMatch': True,
        }), 200
