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

## Planned (Not Yet Built)
- React SPA with 4 tabs: Explore, My List (CRM), Reminders, Profile
- Anthropic Claude integration (Search call + Deep Dive narrative)
- Coach email template (deterministic)
- Vibe questions on Profile tab
- CRM (My List with status, notes, reminders)
- CAMPUS_PHOTOS
