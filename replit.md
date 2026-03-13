# Lane4 — D3 Swim Recruiting Advisor

## Overview
Lane4 is a deterministic swim recruiting analysis tool for D3 college swimming. It scores a swimmer's times against real D3 conference championship benchmark data across 76 programs and 9 conferences.

## Tech Stack
- **Backend**: Python / Flask (serves API + static files)
- **Frontend**: Vanilla HTML/CSS/JS (React SPA planned for next phase)
- **Data**: `data/lane4_swim_model.xlsx` — source of truth for all benchmarks and team data
- **AI**: Anthropic Claude `claude-sonnet-4-20250514` (planned — not yet integrated)

## Architecture

### Backend (`main.py`)
Flask app with two layers:
1. **Data loading** — reads `lane4_swim_model.xlsx` at startup into two in-memory dicts
2. **Scoring API** — `/api/score` runs the full deterministic pipeline

### API Endpoints
| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the frontend SPA |
| `/api/meta` | GET | Returns all conferences, teams per conference, and event list |
| `/api/score` | POST | Scores a swimmer's times at a given school — returns full breakdown |
| `/api/health` | GET | Returns server status + benchmark/team counts |

### Data Model
**`Sheet1` (BENCHMARKS)** — 127 rows, 9 conferences × 14-15 events
- Key: `Conference|Event`
- Columns used: `1st_sec`, `8th_sec`, `16th_sec`, `Sec_per_place`

**`Team_Tiers` (TEAMS)** — 76 rows, one per D3 program
- Key: `Conference|Team`
- Columns used: `PSF`, `Tier`, `Finish`, `MenPoints`
- **Known issue in sheet**: Column 14 duplicates the header `Conference` — loader uses first-occurrence-wins to avoid this

**`Swimmer_Calcs`** — Formula template (no data rows); defines the authoritative scoring logic

## Scoring Engine (authoritative formulas from Swimmer_Calcs)

### Place Estimation (3-zone linear interpolation)
```
if time <= 1st_sec:       place = 1.0
if time <= 8th_sec:       place = 1 + (time - 1st) / (8th - 1st) * 7
if time <= 16th_sec:      place = 8 + (time - 8th) / (16th - 8th) * 8
else:                     place = 16 + (time - 16th) / sec_per_place
```

### Points + Confidence Weighting
```
ExpPoints = MAX(0, MIN(20, 21 - place))
Confidence = 1.0  if place <= 12
             0.85 if place <= 14
             0.65 if place <= 16
             0.0  if place > 16
AdjPoints = ExpPoints × Confidence
```

### Final Score
```
rawPts = sum of top 3 AdjPoints across all entered events
adjPts = rawPts × PSF  (PSF from Team_Tiers lookup)
```

### Swim Tier Labels
| adjPts | Tier |
|---|---|
| < 1 | Moonshot |
| < 4 | Reach |
| < 10 | Recruitable |
| < 18 | Priority Recruit |
| < 35 | Likely Commit |
| < 50 | Conference Star |
| ≥ 50 | High-Point Contender |

## Key Design Decisions
- **Workbook overrides spec** wherever they conflict (formulas, PSF values, 1000 Free availability)
- **PSF values in data**: 0.7, 0.78, 1.0, 1.1, 1.2 (no 0.85 as spec mentioned)
- **1000 Free** benchmark only exists in NESCAC (spec said 4 conferences)
- **Team_Tiers has truncated names** in some rows (e.g., `Rochester Institute of Technol`) — SCHOOL_META must use these exact strings as keys

## Conferences & Programs
9 conferences, 76 total programs:
Centennial (8), Liberty League (10), MIAC (6), NCAC (9), NESCAC (11), NEWMAC (7), NWC (8), SCIAC (9), UAA (8)

## Planned (Not Yet Built)
- React SPA with 4 tabs: Explore, My List (CRM), Reminders, Profile
- Hardcoded JAMES profile constant
- SCHOOL_META (55 schools with academic/location data)
- Admission scoring (`admissionChance()`)
- Anthropic Claude integration (Search call + Deep Dive narrative)
- Flask backend becomes Claude API proxy
