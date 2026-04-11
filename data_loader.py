"""data_loader.py — CSV-based data loading pipeline for Lane4.
Populates mutable state singletons from output/*.csv files at startup."""
import os, re, csv as _csv, json
from collections import defaultdict
from scoring.primitives import _float
from state import BENCHMARKS, TEAMS, TEAMS_LIST, CONFERENCES, NORMALIZATION_LOG, EXPLORE_SCHOOLS, SCHOOL_LOCATIONS
from models.school_data import SCHOOL_META, TEAM_NAME_MAP, CONF_DIVISION
from models.school_aliases import _UAA_SHORT

CSV_BENCH_PATH = os.path.join(os.path.dirname(__file__), 'output', 'all_event_anchors.csv')
CSV_SNAP_PATH  = os.path.join(os.path.dirname(__file__), 'output', 'lane4_snapshot_compatible.csv')

def load_data():
    # all_event_anchors.csv is the canonical benchmark source.
    # Legacy Excel benchmark loading has been retired.
    # If benchmark gaps exist, they must be fixed in the master CSV — not patched at runtime.
    _load_benchmarks()
    _load_teams_from_csv()
    _load_conf_tier_lookup()


def _load_benchmarks():
    """
    Load BENCHMARKS from output/all_event_anchors.csv — the single canonical source.
    Fails loudly if the file is missing, malformed, or contains non-monotonic rows.
    Men's rows only; BENCHMARKS keys carry no gender suffix.
    """
    import csv as _csv

    _REQUIRED_COLS = {'Conference', 'Gender', 'Event', '1st', '8th', '16th'}

    if not os.path.exists(CSV_BENCH_PATH):
        raise FileNotFoundError(
            f"FATAL: Benchmark CSV not found: {CSV_BENCH_PATH}\n"
            "output/all_event_anchors.csv is the canonical benchmark source.\n"
            "Do not attempt to reconstruct from any legacy file."
        )

    with open(CSV_BENCH_PATH, newline='', encoding='utf-8') as f:
        reader = _csv.DictReader(f)
        actual_cols = set(reader.fieldnames or [])
        missing = _REQUIRED_COLS - actual_cols
        if missing:
            raise ValueError(
                f"FATAL: output/all_event_anchors.csv is missing required columns: {missing}\n"
                f"Found columns: {sorted(actual_cols)}"
            )
        rows = list(reader)

    invalid_count = 0
    loaded_count = 0
    for row in rows:
        if (row.get('Gender') or '').strip().lower() != 'men':
            continue
        conf  = (row.get('Conference') or '').strip()
        event = (row.get('Event') or '').strip()
        if not conf or not event:
            continue

        # Use pre-converted *_seconds columns when available; fall back to raw columns
        first     = _float(row.get('1st_seconds') or row.get('1st'))
        eighth    = _float(row.get('8th_seconds') or row.get('8th'))
        sixteenth = _float(row.get('16th_seconds') or row.get('16th'))
        spp       = _float(row.get('Sec_per_place'))

        if first is None and eighth is None:
            continue  # no usable benchmark data

        # Monotonic validation: 1st ≤ 8th ≤ 16th
        if first is not None and eighth is not None and sixteenth is not None:
            if not (first <= eighth <= sixteenth):
                print(
                    f"[WARN] Benchmark monotonicity violation: "
                    f"{conf} | {event} | 1st={first} 8th={eighth} 16th={sixteenth}"
                )
                invalid_count += 1

        BENCHMARKS[f"{conf}|{event}"] = {
            'first':         first,
            'eighth':        eighth,
            'sixteenth':     sixteenth,
            'sec_per_place': spp,
        }
        loaded_count += 1

    if invalid_count:
        print(f"[WARN] {invalid_count} benchmark row(s) failed monotonic validation — see above.")
    print(f"[benchmarks] Loaded {loaded_count} rows from output/all_event_anchors.csv "
          f"({invalid_count} invalid).")


def _load_teams_from_csv():
    """
    Load TEAMS, TEAMS_LIST, and CONFERENCES from output/lane4_snapshot_compatible.csv.
    Men's rows only. Fails loudly if the file is missing.
    """
    import csv as _csv

    if not os.path.exists(CSV_SNAP_PATH):
        raise FileNotFoundError(
            f"FATAL: Snapshot CSV not found: {CSV_SNAP_PATH}"
        )

    loaded_count = 0
    with open(CSV_SNAP_PATH, newline='', encoding='utf-8') as f:
        for row in _csv.DictReader(f):
            if (row.get('gender') or '').lower() != 'men':
                continue
            conf = (row.get('Conference') or '').strip()
            raw  = (row.get('Team') or '').strip()
            if not conf or not raw or conf == 'Unknown':
                continue

            canonical = raw
            normalized = False
            if raw in TEAM_NAME_MAP:
                canonical, reason = TEAM_NAME_MAP[raw]
                normalized = True
                NORMALIZATION_LOG.append({
                    'raw': raw, 'canonical': canonical,
                    'reason': reason, 'conference': conf,
                })

            key = f"{conf}|{canonical}"
            if key in TEAMS:
                continue  # deduplicate

            try:
                finish = int(row.get('Finish') or 0) or None
            except (ValueError, TypeError):
                finish = None

            team_rec = {
                'conference':       conf,
                'school':           canonical,
                'raw_name':         raw,
                'psf':              _float(row.get('PSF')) or 1.0,
                'tier':             row.get('Tier') or '',
                'finish':           finish,
                'men_points':       _float(row.get('MenPoints')),
                'normalized':       normalized,
                'conf_tier_short':  row.get('tier_short') or '',
                'conf_tier':        row.get('final_tier') or '',
                'conf_finish_2026': finish,
                'conf_score_2026':  row.get('MenPoints') or '',
                'conf_power_class': row.get('PowerClass') or '',
            }
            TEAMS[key] = team_rec
            TEAMS_LIST.append(team_rec)

            if conf not in CONFERENCES:
                CONFERENCES[conf] = []
            if canonical not in CONFERENCES[conf]:
                CONFERENCES[conf].append(canonical)
            loaded_count += 1

    # Sort teams within each conference by finish position
    for conf in CONFERENCES:
        CONFERENCES[conf].sort(
            key=lambda s: TEAMS.get(f"{conf}|{s}", {}).get('finish') or 99
        )
    print(f"[teams] Loaded {loaded_count} team records from output/lane4_snapshot_compatible.csv")


def _norm_key(s):
    """Lowercase, strip all non-alphanumeric for fuzzy matching."""
    import re
    return re.sub(r'[^a-z0-9]', '', s.lower().strip()) if s else ''


def _load_conf_tier_lookup():
    """
    Reads output/lane4_snapshot_compatible.csv and enriches each team_rec in
    TEAMS_LIST with 2026 conference championship data:
      conf_tier_short  — '1A' / '1B' / '2' / '3' / '4'  (empty if not in snapshot)
      conf_tier        — 'Tier 1A' … 'Tier 4'
      conf_finish_2026 — integer conference finish rank
      conf_score_2026  — team score at the 2026 championship
      conf_power_class — 'Super Powerhouse' / 'Powerhouse' / ''
    """
    import csv as _csv
    snap_path = os.path.join(os.path.dirname(__file__), 'output', 'lane4_snapshot_compatible.csv')
    if not os.path.exists(snap_path):
        return

    # Build lookup: norm_conf|norm_team → row  (men's rows preferred)
    snap_rows = []
    with open(snap_path, newline='', encoding='utf-8') as f:
        snap_rows = list(_csv.DictReader(f))

    exact = {}   # norm(conf)|norm(team) → row
    by_team = {} # norm(team) → list of rows
    for row in snap_rows:
        if row.get('gender', '').lower() != 'men':
            continue
        ck = _norm_key(row['Conference']) + '|' + _norm_key(row['Team'])
        exact[ck] = row
        by_team.setdefault(_norm_key(row['Team']), []).append(row)

    def _find(conf, school):
        # 1. exact conf|team match
        ck = _norm_key(conf) + '|' + _norm_key(school)
        if ck in exact:
            return exact[ck]
        # 2. UAA abbreviation table
        norm_school = _norm_key(school)
        full_name = _UAA_SHORT.get(norm_school)
        if full_name:
            rows = by_team.get(_norm_key(full_name), [])
            if rows:
                return rows[0]
        # 3. team-name match, same conference
        candidates = by_team.get(norm_school, [])
        same_conf = [r for r in candidates if _norm_key(r['Conference']) == _norm_key(conf)]
        if same_conf:
            return same_conf[0]
        # 4. team-name match, any conference (only if unique)
        if len(candidates) == 1:
            return candidates[0]
        return None

    for team_rec in TEAMS_LIST:
        hit = _find(team_rec['conference'], team_rec['school'])
        # Fallback: try the original raw Excel name (before TEAM_NAME_MAP normalization)
        if not hit and team_rec.get('raw_name') and team_rec['raw_name'] != team_rec['school']:
            hit = _find(team_rec['conference'], team_rec['raw_name'])
        if hit:
            try:
                finish_2026 = int(hit['Finish'])
            except (ValueError, TypeError):
                finish_2026 = None
            team_rec['conf_tier_short']  = hit.get('tier_short', '')
            team_rec['conf_tier']        = hit.get('final_tier', '')
            team_rec['conf_finish_2026'] = finish_2026
            team_rec['conf_score_2026']  = hit.get('MenPoints', '')
            team_rec['conf_power_class'] = hit.get('PowerClass', '')
        else:
            team_rec['conf_tier_short']  = ''
            team_rec['conf_tier']        = ''
            team_rec['conf_finish_2026'] = None
            team_rec['conf_score_2026']  = ''
            team_rec['conf_power_class'] = ''

    _build_explore_schools()


def _build_explore_schools():
    """
    Build EXPLORE_SCHOOLS — one record per unique school across the full
    2026 championship snapshot (~324 schools).  Every school gets the SAME
    object shape.  SCHOOL_META is an enrichment source; its absence never
    changes the code path — only the richness of the values.
    """
    import csv as _csv
    from collections import defaultdict

    EXPLORE_SCHOOLS.clear()

    snap_path = os.path.join(os.path.dirname(__file__), 'output', 'lane4_snapshot_compatible.csv')
    if not os.path.exists(snap_path):
        return

    snap_rows = []
    with open(snap_path, newline='', encoding='utf-8') as f:
        snap_rows = list(_csv.DictReader(f))

    # Build per-school, per-gender rows
    by_school = defaultdict(dict)   # school_name → {'men': row, 'women': row}
    for row in snap_rows:
        gender = row.get('gender', '').lower()
        school = row.get('Team', '').strip()
        if school in TEAM_NAME_MAP:
            school, _ = TEAM_NAME_MAP[school]   # normalize PDF team names to canonical
        if school and gender:
            by_school[school][gender] = row

    # Modeled lookup by norm of canonical name AND raw name.
    # Also add _UAA_SHORT reverse mapping so snapshot full-names (e.g. "Emory University")
    # correctly resolve to their abbreviated TEAMS_LIST entries (e.g. "Emory").
    modeled_by = {}
    for tr in TEAMS_LIST:
        modeled_by[_norm_key(tr['school'])] = tr
        raw = tr.get('raw_name', '')
        if raw:
            modeled_by[_norm_key(raw)] = tr
    # Reverse UAA_SHORT: short_norm → full_name; add full_name → team_rec
    for short_norm, full_name in _UAA_SHORT.items():
        if short_norm in modeled_by:
            modeled_by[_norm_key(full_name)] = modeled_by[short_norm]

    def _si(v):
        try:   return int(v)
        except: return None

    def _sf(v):
        try:   return float(v)
        except: return None

    tier_order = {'1A': 0, '1B': 1, '2': 2, '3': 3, '4': 4, '': 5}

    for school, gmap in by_school.items():
        men_row   = gmap.get('men')
        women_row = gmap.get('women')
        primary   = men_row or women_row
        if not primary:
            continue

        tr = modeled_by.get(_norm_key(school))

        men_ts   = men_row.get('tier_short', '')   if men_row   else ''
        women_ts = women_row.get('tier_short', '') if women_row else ''
        ts       = men_ts or women_ts

        raw_conf = primary.get('Conference', '')
        # If the snapshot CSV recorded 'Unknown' (unrecognised PDF conference),
        # fall back to the team_rec's conference so scoring and display use the
        # correct real conference name.
        display_conf = (tr['conference']
                        if tr and raw_conf in ('Unknown', '', None)
                        else raw_conf)

        entry = {
            'school':            school,
            'conference':        display_conf,
            'conf_tier_short':   ts,
            'conf_tier':         (men_row or women_row).get('final_tier', ''),
            'conf_power_class':  (men_row or women_row).get('PowerClass', ''),
            'men_finish_2026':   _si(men_row['Finish'])          if men_row   else None,
            'women_finish_2026': _si(women_row['Finish'])        if women_row else None,
            'men_score_2026':    _sf(men_row.get('MenPoints',''))   if men_row   else None,
            'women_score_2026':  _sf(women_row.get('MenPoints','')) if women_row else None,
            'men_tier_short':    men_ts,
            'women_tier_short':  women_ts,
            'gender_coverage':   sorted(gmap.keys()),
        }

        # Unified meta merge — SCHOOL_META is an enrichment source, not a gate.
        # Try direct school name first, then canonical team name.
        meta_raw = SCHOOL_META.get(school) or (SCHOOL_META.get(tr['school']) if tr else None) or {}
        entry['meta'] = {
            'accept':    meta_raw.get('accept'),
            'satMedian': meta_raw.get('satMedian'),
            'location':  meta_raw.get('location', ''),
            'hiddenIvy': meta_raw.get('hiddenIvy', False),
            'ivyLeague': meta_raw.get('ivyLeague', False),
            'stem':      meta_raw.get('stem', False),
            'merit':     meta_raw.get('merit', ''),
            'vibe':      meta_raw.get('vibe', ''),
        }
        if meta_raw.get('moonshot'):
            entry['meta']['moonshot'] = True

        if tr:
            entry['row_type']    = 'modeled_school'
            entry['psf']         = tr.get('psf', 1.0)
            entry['tier']        = tr.get('tier', '')
            entry['hasSwimData'] = True
            entry['_team_rec']   = tr   # stored so build_school_universe() re-uses the match
        else:
            entry['row_type']    = 'snapshot_only'
            entry['hasSwimData'] = False
            entry['_team_rec']   = None

        EXPLORE_SCHOOLS.append(entry)

    # Sort: tier (1A first), then men's finish, then school name
    EXPLORE_SCHOOLS.sort(key=lambda s: (
        tier_order.get(s.get('conf_tier_short', ''), 5),
        s.get('men_finish_2026') or s.get('women_finish_2026') or 99,
        s['school'],
    ))


