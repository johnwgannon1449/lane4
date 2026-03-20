# Lane4 — D3 Swim Recruiting Advisor

## Overview
Lane4 is a deterministic swim recruiting analysis tool for D3 college swimming. It scores a swimmer's times against real D3 conference championship benchmark data across 76 programs and 9 conferences, then uses Anthropic Claude for personalized narratives. Currently: Flask backend + vanilla JS frontend (React SPA planned for next phase).

## Tech Stack
- **Backend**: Python / Flask (serves API + static files)
- **Frontend**: Vanilla HTML/CSS/JS (React SPA planned for next phase)
- **Data**: `data/lane4_swim_model.xlsx` — source of truth for all benchmarks and team data
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
**`Sheet1` (BENCHMARKS)** — 127 rows, 9 conferences × 14-15 events
- Key: `Conference|Event`
- Columns used: `1st_sec`, `8th_sec`, `16th_sec`, `Sec_per_place`

**`Team_Tiers` (TEAMS)** — 76 rows, one per D3 program
- Key: `Conference|canonical_name`
- Columns used: `PSF`, `Tier`, `Finish`, `MenPoints`
- **Known workbook issue**: Column 14 duplicates header `Conference` — loader uses first-occurrence-wins

**`Swimmer_Calcs`** — Formula template (no data rows); defines the authoritative scoring logic

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

Non-school-name queries use a 5-step generative pipeline (direct school-name
lookups use the original similarity-sort + Claude-picks-5 path, unchanged).

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

### Active Fixes Applied
- **CID ligature normalization**: `(cid:976)` → `"f"` in HY-TEK MM8 fonts; resolves "Butter(cid:976)ly" → "Butterfly" for event header detection
- **LAST-time extraction**: `parse_place_and_time` returns the last valid time (≥10 s) per row, correctly picking the finals time in "Prelim Time | Finals Time" dual-column rows
- **`consecutive_depth` property**: unbroken run from place 1 — immune to spurious loose-scan bleed-in
- **Mode B gender detection**: samples both first 8 AND last 8 pages; state machine upgrades `"women"` → `"combined"` when men's event header encountered during parse
- **`_detect_column_splits` fallback pass**: when primary 3-bin gap detection finds nothing, a secondary pass searches the central 40–60% zone with gap_len ≥ 2 and 50 px merge tolerance. Requires ≥ 15 words on each side. Fixes distance-event transition pages where adjacent-column split-row times partially fill the gap (MPSF Men/Women 100 Free; PCSC Men 500/1650/200 Back/200 Breast, Women 500 Free — all now captured).
