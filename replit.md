# Lane4 — D3 Swim Recruiting Advisor

## Overview
Lane4 is a deterministic swim recruiting analysis tool for D3 college swimming. It scores a swimmer's times against real D3 conference championship benchmark data across 76 programs and 9 conferences, then uses Anthropic Claude for personalized narratives. Currently: Flask backend + vanilla JS frontend (React SPA planned for next phase).

## Tech Stack
- **Backend**: Python / Flask (serves API + static files)
- **Frontend**: Vanilla HTML/CSS/JS (React SPA planned for next phase)
- **Data**: CSV-only (Excel retired); two canonical CSVs — see Data section below
- **AI**: Anthropic Claude `claude-sonnet-4-20250514` (planned — not yet integrated)

## Architecture

### Backend (`main.py`)
Flask app with three layers:
1. **Data loading** — reads `lane4_swim_model.xlsx` at startup into BENCHMARKS + TEAMS + TEAMS_LIST
2. **Scoring engine** — deterministic pipeline: parseTime → estimatePlace → expPoints × confidence → PSF → admissionChance
3. **API** — exposes /api/meta, /api/score, /api/score-all, /api/health

### API Endpoints
| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the frontend SPA |
| `/api/meta` | GET | Conferences, teams, events, normalization log |
| `/api/score` | POST | Score arbitrary times at one specific school |
| `/api/score-all` | GET | Score James against all 76 programs, returns ranked list |
| `/api/health` | GET | Server status, counts |

### Data Model
**`output/all_event_anchors.csv` (BENCHMARKS)** — 886 rows (447 men + 439 women), ~33 conferences × 14 events × 2 genders
- Key: `Conference|Gender|Event`
- Columns used: `1st`, `8th`, `16th`, `1st_seconds`, `8th_seconds`, `16th_seconds`, `Sec_per_place`
- Loaded via `_load_benchmarks()` — validates 1st ≤ 8th ≤ 16th monotonicity at startup, logs violations
- **Fully repaired**: 37 rows fixed (19 men's + 18 women's 8th_seconds conversion bug; 6 Category B corrupted anchors restored from source PDFs); 2 duplicate rows removed

**`output/lane4_snapshot_compatible.csv` (TEAMS)** — 245 team records, one per program
- Key: `canonical_name`
- Columns used: `PSF`, `Tier`, `Finish`, `MenPoints`, `Conference`
- Supplement: programs not in anchor CSV default to `psf=1.0`

**Excel fully retired** — `openpyxl` dependency removed; no XLSX file referenced at runtime

## Scoring Engine (authoritative formulas from Swimmer_Calcs workbook)

### Place Estimation (3-zone linear interpolation)
```
if time <= 1st_sec:       place = 1.0   (capped — workbook IF formula)
if time <= 8th_sec:       place = 1 + (time - 1st) / (8th - 1st) * 7
if time <= 16th_sec:      place = 8 + (time - 8th) / (16th - 8th) * 8
else:                     place = 16 + (time - 16th) / sec_per_place
```

### Points + Confidence Weighting
```
ExpPoints  = MAX(0, MIN(20, 21 - place))
Confidence = 1.0  if place <= 12
             0.85 if place <= 14
             0.65 if place <= 16
             0.0  if place > 16
AdjPoints  = ExpPoints × Confidence
```

### Final Score
```
rawPts  = sum of top-3 AdjPoints across all entered events
adjPts  = rawPts × PSF   (PSF from Team_Tiers lookup)
```

### Swim Tier Labels (adjPts thresholds — from workbook)
| adjPts | Tier |
|---|---|
| < 1 | Moonshot |
| < 4 | Reach |
| < 10 | Recruitable |
| < 18 | Priority Recruit |
| < 35 | Top Recruit |
| < 50 | Conference Star |
| ≥ 50 | High-Point Contender |

### Admission Scoring v2 — Matrix Model
```
# 1. School selectivity tier (from accept% + satMedian)
#    ultra_selective | highly_selective | selective | broader_admit

# 2. SAT ranges estimated: sat25 = satMedian-60, sat75 = satMedian+60
#    sat_floor = sat25 - (60|80|100|120 by tier)

# 3. Academic band (0-4) from SAT subscore (-2 to +2) + GPA subscore (0 — no percentile data)
#    SAT > sat75 → +2 | sat25-sat75 → +1 | floor-sat25 → 0 | below floor → -2
#    raw → band: 4→4, 2-3→3, 0-1→2, -1to-2→1, ≤-3→0
#    Hard stop: GPA < 2.0 → band 0

# 4. Swim support band (0-4)
#    High-Point Contender / Conference Star → 4
#    Priority Recruit / Top Recruit → 3
#    Recruitable → 2 | Reach → 1 | Moonshot → 0
#    PSF modifier: >1.00 → +1, ≤0.78 → -1, else 0 (clamped 0-4)

# 5. Base label from 5×5 matrix, then guardrails (gpa/sat floors, selectivity caps)
```
Labels (6): Very Strong Chance, Strong Chance, Realistic Shot, Possible, Major Reach, Moonshot
Moonshot override: MIT and Caltech return "Moonshot — Apply for Fun" unconditionally.

## Name Normalization (10 known workbook truncations — explicit, flagged)
| Raw (workbook) | Canonical | Reason |
|---|---|---|
| McDaniel College Swim Team | McDaniel College | trailing qualifier |
| Rochester Institute of Technol | Rochester Institute of Technology | truncated at 30 chars |
| Rensselaer Polytechnic Institu | Rensselaer Polytechnic Institute | truncated at 30 chars |
| Union College (New York) | Union College | parenthetical qualifier |
| Massachusetts Institute of Tec | MIT | truncated — schema uses 'MIT' |
| Worcester Polytechnic Institut | Worcester Polytechnic Institute | truncated at 30 chars |
| Wheaton College (Ma) | Wheaton College (MA) | wrong casing |
| Whitworth University Swim Team | Whitworth University | trailing qualifier |
| California Institute of Techno | Caltech | truncated — schema uses 'Caltech' |
| Saint Johns University | Saint John's University | missing apostrophe |

All normalizations are logged in NORMALIZATION_LOG and returned in /api/meta.

## JAMES Profile (hardcoded)
```python
JAMES = {
    "name": "James", "gpa": 4.0, "sat": 1460,
    "satProjected": 1500, "mathSat": 720, "mathSatProjected": 760,
    "times": {
        "1650 Free": "16:06", "1000 Free": "9:30", "500 Free": "4:37",
        "200 Free": "1:43", "400 IM": "4:09", "200 IM": "1:56",
        "100 Breast": "59.5", "50 Breast (Relay Split)": "25.68",
    },
    "vibe": { "campus": "Small and tight-knit…", "friday": "Library…", … }
}
```

## SCHOOL_META
76 entries — all programs in TEAMS have metadata. Fields: accept%, satMedian, hiddenIvy, stem, merit, location, vibe, moonshot (MIT and Caltech only). Keys match canonical names after TEAM_NAME_MAP normalization.

**Hidden Ivy schools** (17): Johns Hopkins, Swarthmore, Vassar, Denison, Kenyon, Oberlin, Williams, Tufts, Amherst, Bates, Hamilton, Bowdoin, Middlebury, Colby, Wesleyan, Pomona-Pitzer, Claremont-Mudd-Scripps

**STEM schools** (14+): Johns Hopkins, Swarthmore, RIT, RPI, Clarkson, Union, Tufts, MIT, USCGA, WPI, Clark, Pomona-Pitzer, CMS, Caltech, Washington (Mo), Carnegie Mellon, Case Western, Rochester (UAA)

**Moonshot schools** (2): MIT, Caltech

## Key Design Decisions
- **Workbook overrides spec** wherever they conflict (formulas, PSF values, place-cap at 1.0)
- **PSF values**: 0.7, 0.78, 1.0, 1.1, 1.2 (no 0.85 as spec mentioned — workbook authoritative)
- **1000 Free benchmark** only exists in NESCAC (spec said Liberty League/MIAC/NESCAC/SCIAC — workbook is authoritative)
- **UAA names** are intentionally abbreviated (Emory, NYU, Chicago, etc.) — not truncations
- **Schools with rawPts = 0 excluded** from score-all results (zero-point exclusion guardrail)

## Conferences & Programs
9 conferences, 76 total programs:
Centennial (8), Liberty League (10), MIAC (6), NCAC (9), NESCAC (11), NEWMAC (7), NWC (8), SCIAC (9), UAA (8)

## Current Frontend
Single-page app at `/` with:
1. **James profile strip** — GPA, SAT, times
2. **Normalization alert** — lists all 10 workbook name fixes with reason and source
3. **Ranked results table** — all 76 schools, sortable/filterable, expandable rows showing per-event breakdown (place, expPts, confidence, adjPts, rawPts, PSF, adjPts total, admission scores)
4. **Filter buttons**: All, Hidden Ivy, STEM, High Merit, Normalized
5. **Manual calculator** — enter any times at any school for ad-hoc debugging

## Search Architecture — AI-First Pipeline (`/api/search`)

Non-school-name queries use a 5-step generative pipeline. School-name queries
use a new liberal resolver described in **School-Name Resolution** below.

### School-Name Resolution (`_resolve_school_names`)
Added June 2025, redesigned Phase 2. General school-entity search — no giant handcrafted alias table.

**Functions (main.py ~line 3597):**
- `_qnorm(s)` — normalizes query: lowercase, strip punctuation, `&`→`and`
- `_ACRONYM_ALIASES` — ~40-entry table of **genuine gaps only**: pure initials (CMU, JHU, UCSB, UCLA, WPI, RPI, RIT, CWRU, NJIT, NYU, GWU), nickname contractions (WashU/WUSTL), stored-abbreviation mismatches (VA Tech, NC State, Georgia Tech), and known ambiguous multi-candidate entries (rochester, augustana, wheaton)
- `_US_STATES` — state abbreviation → full name dict (51 entries) for surface matching
- `_school_entity_surface(record)` — builds normalized search text per school: canonical name + city + state abbreviation + full state name (all from `meta.location`); 323/323 coverage
- `_resolve_school_names(query, all_results)` — **6-pass entity resolver**:
  1. Acronym alias → score 1.0
  2. Exact normalized name → score 1.0
  3. Name substring (both directions, min 4 chars) → score 0.80–0.85
  4. Prefix-token match (all query tokens must be prefixes of school-name tokens) → score 0.78; catches truncated canonicals: "Penn State"→"Pennsylvania State University", "Georgia Tech"→"Georgia Institute of Technolog", "Johns Hopkin"→Johns Hopkins
  5. difflib fuzzy against names → cutoff 0.80 (tight to avoid false friends like Worcester/Rochester)
  6. City/state surface fallback → score 0.65, **only fires when passes 1–5 return nothing**; enables city-based queries: "Nashville"→Vanderbilt, "Pittsburgh"→Pitt
  - Confidence gate: MIN_CONF = 0.55

**`search()` endpoint behavior:**
- `len(resolved) == 1` → `direct_match` → original similarity-sort + Claude-picks-5 path
- `len(resolved) >= 2` → multi-match early return: `{ answer, schools, directMatch: false, multiMatch: true }` — no AI call; user sees all plausible matches as cards and picks the right one
- `len(resolved) == 0` → falls through to out-of-universe stub (Harvard, Stanford, etc.)

Non-school-name queries use a 5-step generative pipeline (unchanged).

**Step 1 — Query classification** (`_classify_query_mode`)
Returns GUIDED / CONSTRAINED / OBJECTIVE / EXPLORATORY based on signal words.
- GUIDED: "for me", "where should I", "best schools for swimming"
- CONSTRAINED: "where I can get in", "near me"
- OBJECTIVE: "best colleges in America", "top 10", "US News"
- EXPLORATORY (default): broad discovery

**Step 2 — Candidate generation** (`_build_candidate_prompt`)
Calls Claude with a counselor-mode prompt:
- Returns 12-18 school name strings + a 1-2 sentence `answer`
- GUIDED/CONSTRAINED: passes student context (GPA, SAT, events, vibe) but
  instructs Claude NOT to filter by admissibility or swim fit
- OBJECTIVE/EXPLORATORY: no student context passed

**Step 3 — Map to universe** (`_map_to_universe`)
Fuzzy-matches LLM-generated names to Lane4's 324-school dataset via:
  1. Exact normalized match
  2. Substring match
  3. Key-token Jaccard ≥ 0.50
Ignores candidates that don't map confidently.

**Step 4 — Existing scoring applied**
School records already carry full scoring from `build_school_universe()`.
No re-scoring needed; admissions + swim labels are already on each record.

**Step 5 — Rank and return** (`_search_rank_score`)
- GUIDED/CONSTRAINED: `adm_s × 0.60 + swim_n × 0.40`
  - swim_n uses adjPts if > 0; 0 if below bar or no data (never inflates)
  - Acceptance-rate tie-breaker: prefer broader-admit within same adm band
- OBJECTIVE/EXPLORATORY: `swim_n × 0.65 + adm_s × 0.35` (program strength leads)
Returns top 6 (up to 12 if user asks for more).

**Fallback**: if candidate mapping collapses to < 3 schools, falls back to
`_pre_sort()` (the original keyword-based top-35 pool). Direct-match path
(school name search → similarity sort → Claude picks 5 similar) is untouched.

---

## Deep Dive System (`/api/deep-dive`)

Generates an 8-to-12-section narrative for a single school. Powered by Claude `claude-sonnet-4-6`, max 2400 tokens.

### Personalization Inputs
| Input | Source | How Used |
|---|---|---|
| `primaryMajor` | JAMES profile (structured text field) | Drives academic section depth and "More: [Field]" expanded section |
| `secondaryMajor` | JAMES profile (optional) | Combined with primary if present |
| `vibeAnswers` | VIBE_STATE (structured dropdown) | Shapes Campus Life tone; light influence on Student Experience |
| `otherPrefs` | OTHER_PREFS (free-form textarea) | Signals extracted for Student Experience and Campus Life |

### Section Order
1. Bottom Line (2-3 sentences, always)
2. In the Pool (swim-universe only)
3. Coach Interest (swim-universe only)
4. What [School] Is Known For
5. If You're Serious About [Major] (only when major is known; 4-5 sentences, specific)
6. **More: [Major]** (expandable; 6-8 sentences going deeper — class size, internships, labs, outcomes, tradeoffs)
7. Are You Admissible? (with admission comparison table injected by frontend)
8. What It Costs (uses exact MONEY DATA figures)
9. Campus Life (3-4 sentences, vibe-informed)
10. **More: Student Experience** (expandable; 4-6 sentences, only when vibe/free-response signals present)

### "More" Expandable Sections
- Pre-generated in same API call; hidden behind toggle button in frontend
- Academic "More" section: always generated when a major is known; never for swim
- Student Experience "More": only generated when meaningful free-response signals exist
- `_ddToggleMore()` in frontend toggles visibility and button label

### Key Prompt Rules
- No em dashes anywhere
- No brochure language, no generic positivity
- No overpersonalization (never "for a student like you" or "because you said X")
- Swim fit explained ONCE only (in "In the Pool")
- "More:" sections do not repeat the parent section — go further
- Prompt file: `prompts/lane4_deep_dive_prompt.txt`

---

## Data Harvester — `harvester.py`

Batch-parses NCAA conference championship PDFs into structured CSV anchor data.

### Architecture
- **`parser_helpers.py`** — PDF extraction, event header detection, gender classification, time parsing, column-mode routing
- **`harvester.py`** — State machine, EventAccumulator, bundle merging, CSV output
- **`conference_map.json`** — Filename→conference name mapping, bundle grouping
- **Input**: `input_pdfs/` — one or more PDFs per conference bundle
- **Output**: `output/event_anchors.csv`, `output/event_coverage_report.csv`, `output/review_flags.csv`, `output/debug_bundle_report.csv`

### Anchor Schema (`event_anchors.csv`)
`Conference, Year, Gender, Bundle_ID, Event, 1st, 8th, 16th, 1st_seconds, 8th_seconds, 16th_seconds, Sec_per_place, Source_File, Data_Quality, 1st_flags, 8th_flags, 16th_flags`

The `*_flags` columns carry raw annotation suffixes stripped from time tokens (e.g. `*` = conference record, `# NCAA B` = cut standard).

### Parse Modes
| Mode | Trigger | Description |
|---|---|---|
| `normal` | default | pdfplumber `extract_text()` — works for most conferences |
| `multi_column_2` | ODAC | 2-column extraction with y-coordinate row pairing |
| `multi_column_3` | Patriot | 3-column extraction with fixed pixel boundaries |

### Known Conference-Specific Configuration
| Item | Value | Rationale |
|---|---|---|
| `CONFERENCE_PARSE_MODE["ODAC"]` | `multi_column_2` | ODAC PDFs render in 2-column layout |
| `CONFERENCE_PARSE_MODE["Patriot"]` | `multi_column_3` | Patriot PDFs render in 3-column layout |
| `_PATRIOT_COL_BOUNDARIES` | `(196.0, 400.0)` | Hardcoded pixel column splits for Patriot PDF |
| `LIKELY_NOT_CONTESTED["ODAC"]` | `{"1000 Free"}` | Confirmed absent from championship program |
| `LIKELY_NOT_CONTESTED["Patriot"]` | `{"1000 Free"}` | Confirmed absent from championship program |
| `LIKELY_NOT_CONTESTED["Summit League"]` | `{"1000 Free"}` | Confirmed absent from championship program |

### Tolerance Mechanisms (conference-agnostic)
- **DQ-gap interpolation**: if anchor place *p* is absent but neighbors *p-1* and *p+1* are both present, linearly interpolate (handles DQ/DNS gaps)
- **Small-field tolerance**: if all places 1…max are present (max ≥ 8, max < target anchor), substitute max-place time for the missing anchor
- **Pending-place**: if a swimmer name row has no inline time, hold the place and pair it with the time on the immediately following line

### Output CSVs
| File | Description |
|---|---|
| `event_anchors.csv` | One row per captured event (place 1/8/16 times in seconds + formatted) |
| `event_coverage_report.csv` | One row per event per gender per bundle — includes `Coverage_Status`, field depth |
| `review_flags.csv` | Per-event quality notes, time suffix handling, recovery log |
| `fallback_usage.csv` | Anchors using prelim or non-finals data |
| `event_header_debug.csv` | State-machine trace for every event header seen |
| `column_mode_debug.csv` | Per-page column detection log (ODAC/Patriot only) |
| `debug_bundle_report.csv` | Raw extraction rows before merge |
| `debug_bundle_summary.csv` | Per-bundle summary counts |

### Coverage_Status Classification (event_coverage_report.csv)
| Status | Meaning |
|---|---|
| `captured` | Event extracted with all required anchors |
| `missing_optional_1000` | 1000 Free — optional, never penalises score |
| `missing_likely_not_contested` | Event absent from this conference's meet program |
| `missing_data_ceiling` | Event exists but meet field too shallow for target anchor depth (consecutive_depth < 16) |
| `missing_true_parser_miss` | Event should be present and deep enough but parser failed to recover |

### Field Depth Columns (event_coverage_report.csv)
- `Swimmer_Count` — distinct place entries in the best accumulator
- `Deepest_Place_Recovered` — absolute maximum place seen
- `Target_Anchor_Place` — always 16 (places 1, 8, 16 are required)
- `Anchor_Depth_Achievable_YN` — yes/no/na based on `consecutive_depth >= 16`

`consecutive_depth` is the unbroken consecutive run from place 1.  Spurious high-place entries from loose pass-2 scans do not inflate this metric, making it a reliable indicator of true field depth.

### Coverage Results (2026 Championships — current state)
| Conference | Score | Mode | Notes |
|---|---|---|---|
| GLIAC | 28/28 | normal | Baseline — perfect |
| ODAC | 26/28 | multi_column_2 | 1000 Free not contested |
| Patriot | 26/28 | multi_column_3 | 1000 Free not contested |
| Summit League | 26/28 | normal | 1000 Free not contested |
| Big East | 26/28 | normal | 1000 Free not contested |
| CCIW | 25/28 | normal | Men 200 Fly: true data ceiling (11 entrants only) |
| MPSF | 26/28 | multi_column_2 | 1000 Free not contested; was 24/28 before fallback gap fix |
| PCSC | 28/28 | multi_column_2 | Perfect score after fallback gap fix |
| WAC | 18/28 | normal | Mixed single/multi-column layout; needs per-page adaptive mode |

CCIW Men 200 Fly: true data ceiling — only 11 swimmers entered; target anchor depth (16) is not achievable from source data.
MPSF and PCSC improvements: `_detect_column_splits` fallback pass fixed distance-event pages where split-row times partially fill the column gap, reducing the observable gap from the required 3 bins to 2 consecutive empty bins.

## User Preferences
- **Always give full URLs** when referencing pages, endpoints, or routes (e.g. `https://lane4-recruit.replit.app/admin/curate`, not just `/admin/curate`)

### Active Fixes Applied
- **CID ligature normalization**: `(cid:976)` → `"f"` in HY-TEK MM8 fonts; resolves "Butter(cid:976)ly" → "Butterfly" for event header detection
- **LAST-time extraction**: `parse_place_and_time` returns the last valid time (≥10 s) per row, correctly picking the finals time in "Prelim Time | Finals Time" dual-column rows
- **`consecutive_depth` property**: unbroken run from place 1 — immune to spurious loose-scan bleed-in
- **Mode B gender detection**: samples both first 8 AND last 8 pages; state machine upgrades `"women"` → `"combined"` when men's event header encountered during parse
- **`_detect_column_splits` fallback pass**: when primary 3-bin gap detection finds nothing, a secondary pass searches the central 40–60% zone with gap_len ≥ 2 and 50 px merge tolerance. Requires ≥ 15 words on each side. Fixes distance-event transition pages where adjacent-column split-row times partially fill the gap (MPSF Men/Women 100 Free; PCSC Men 500/1650/200 Back/200 Breast, Women 500 Free — all now captured).
