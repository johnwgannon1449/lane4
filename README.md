# Lane4 — D3 Swim Recruiting Advisor

**Live app:** https://lane4-recruit.replit.app

AI-powered D3 college swim recruiting analysis. Scores a swimmer's times against real conference championship benchmark data across 76 programs and 9 conferences, then uses Claude to generate personalized narratives, search results, and deep-dive school reports.

---

## Table of Contents

1. [Tech Stack](#tech-stack)
2. [Architecture Overview](#architecture-overview)
3. [API Reference](#api-reference)
4. [Scoring Engine](#scoring-engine)
5. [Admission Model](#admission-model)
6. [Search System](#search-system)
7. [Deep Dive System](#deep-dive-system)
8. [Onboarding Wizard](#onboarding-wizard)
9. [Auth System](#auth-system)
10. [Admin & Image Curator](#admin--image-curator)
11. [Data Model](#data-model)
12. [Data Harvester](#data-harvester)
13. [Key Design Decisions](#key-design-decisions)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3 / Flask |
| Frontend | Vanilla HTML/CSS/JS (single-page app) |
| AI | Anthropic Claude `claude-sonnet-4-6` |
| Database | PostgreSQL (psycopg2) — user auth and admin accounts |
| Deployment | Replit |
| Data | CSV-only (Excel retired) |

**Environment variables required:**
- `ANTHROPIC_API_KEY` — Claude API key
- `DATABASE_URL` — PostgreSQL connection string
- `SESSION_SECRET` — Flask session secret
- `ADMIN_PASSWORD` — bootstraps the initial admin account on first startup

---

## Architecture Overview

`main.py` is the entire backend (~6300 lines). It loads all data at startup and serves a Flask API + static SPA.

**Startup sequence:**
1. `load_data()` — loads BENCHMARKS from `output/all_event_anchors.csv`, TEAMS from `output/lane4_snapshot_compatible.csv`
2. `_load_benchmarks()` — validates 1st ≤ 8th ≤ 16th monotonicity, logs violations
3. `_build_explore_schools()` — builds the 76-school universe with full scoring for James's profile
4. `_init_db_background()` — initializes PostgreSQL tables (non-blocking)
5. Language and deep-dive prompts loaded from `prompts/` directory

**Static files served from `static/`:**
- `index.html` — main SPA
- `login.html` — login/register page
- `admin_curate.html` — image curation admin UI
- `admin_login.html` — admin login page
- `school_images.json` — curated school images (public)
- `candidates_manifest.json` — harvested image candidates (admin only)
- `curated_manifest.json` — admin selections
- `usa_motivational_times_17_18_scy.json` — USA Swimming standards for A/B/BB cuts

---

## API Reference

### Public / Auth

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/` | None | Serves SPA (`index.html`) |
| `GET` | `/login` | None | Serves login page |
| `POST` | `/api/auth/register` | None | Create user account. Body: `{email, password, name}` |
| `POST` | `/api/auth/login` | None | Login. Body: `{email, password}` |
| `POST` | `/api/auth/logout` | Session | Logout |
| `GET` | `/api/auth/me` | Session | Returns `{loggedIn, email, name}` |

### User Data

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/data/load` | Session | Load saved swimmer profile (times, GPA, SAT, vibe) |
| `POST` | `/api/data/save` | Session | Save swimmer profile |

### SwimCloud Integration

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/public/swimcloud/search` | None | Search SwimCloud by name. Param: `q` |
| `GET` | `/api/public/swimcloud/propose` | None | Propose best time set for a swimmer ID. Param: `id` |
| `GET` | `/api/swimcloud/search` | Session | Same as public search (authenticated version) |
| `GET` | `/api/swimcloud/propose` | Session | Same as public propose (authenticated version) |
| `GET` | `/api/swimcloud/check-prs` | Session | Check PR updates for linked swimmer |

### Core Scoring

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/meta` | None | Conferences, teams, events, normalization log |
| `GET` | `/api/schools` | None | Full 76-school universe with scores for current profile |
| `GET` | `/api/school-locations` | None | School lat/lon for map view |
| `GET/POST` | `/api/score-all` | None | Score times against all 76 programs. POST body: `{times, sat, gpa}` |
| `POST` | `/api/rank-events` | None | Rank swimmer's events by relative strength using USA standards |
| `GET` | `/api/health` | None | Server status and record counts |

### AI Features

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/search` | None | Natural-language school search powered by Claude |
| `POST` | `/api/deep-dive` | None | Full school narrative (8-12 sections) powered by Claude |
| `POST` | `/api/deep-dive/academic` | None | Academic deep-dive section only |
| `POST` | `/api/coach-email` | None | Generate a coach outreach email |

### Admin

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/admin` | Session | Admin dashboard |
| `GET` | `/admin/login` | None | Admin login page |
| `POST` | `/api/admin/login` | None | Admin login |
| `POST` | `/api/admin/logout` | Admin session | Admin logout |
| `GET` | `/api/admin/me` | Session | Returns `{is_admin, email}` |
| `GET` | `/api/admin/list-admins` | Admin | List all admin accounts |
| `POST` | `/api/admin/create-admin` | Admin | Create new admin account |
| `GET` | `/admin/curate` | Admin | Image curation UI |
| `GET` | `/api/admin/conferences` | Admin | Conferences + schools for curation sidebar |
| `GET` | `/api/admin/schools` | Admin | Schools with image status |
| `GET` | `/api/admin/candidates/<school>` | Admin | Image candidates for a school |
| `POST` | `/api/admin/fetch-candidates` | Admin | Trigger image harvest for a school |
| `POST` | `/api/admin/prefetch-conference` | Admin | Background prefetch for all schools in a conference |
| `POST` | `/api/admin/save` | Admin | Save curated selections → pushes to `school_images.json` |
| `POST` | `/api/admin/blocklist` | Admin | Ban an image URL globally |
| `POST` | `/api/admin/rebuild-school-images` | Admin | Rebuild public `school_images.json` from curated manifest |
| `GET` | `/api/ua/schools` | Admin | User-facing admin: school list |
| `GET` | `/snapshot` | None | Debug: full data snapshot |
| `GET` | `/debug-ui` | None | Debug UI page |

---

## Scoring Engine

All formulas are authoritative from the Swimmer_Calcs workbook. The workbook overrides the spec wherever they conflict.

### Input Data

**BENCHMARKS** (`output/all_event_anchors.csv`) — 886 rows
- Key: `Conference|Gender|Event`
- Columns: `1st`, `8th`, `16th`, `1st_seconds`, `8th_seconds`, `16th_seconds`, `Sec_per_place`

**TEAMS** (`output/lane4_snapshot_compatible.csv`) — 245 team records
- Key: `canonical_name`
- Columns: `PSF`, `Tier`, `Finish`, `MenPoints`, `Conference`
- Programs not in BENCHMARKS default to `PSF = 1.0`

### Place Estimation (3-zone linear interpolation)

```
if time <= 1st_sec:       place = 1.0   (capped — workbook IF formula)
if time <= 8th_sec:       place = 1 + (time - 1st) / (8th - 1st) × 7
if time <= 16th_sec:      place = 8 + (time - 8th) / (16th - 8th) × 8
else:                     place = 16 + (time - 16th) / sec_per_place
```

### Points + Confidence Weighting

```
ExpPoints  = MAX(0, MIN(20, 21 - place))

Confidence = 1.00  if place <= 12
           = 0.85  if place <= 14
           = 0.65  if place <= 16
           = 0.00  if place > 16

AdjPoints  = ExpPoints × Confidence
```

### Final Score

```
rawPts  = sum of top-3 AdjPoints across all entered events
adjPts  = rawPts × PSF   (PSF from Team_Tiers lookup)
```

**PSF values in use:** `0.70`, `0.78`, `1.0`, `1.1`, `1.2` — no 0.85 (workbook authoritative)

### Swim Tier Labels

| adjPts | Label |
|---|---|
| < 1 | Moonshot |
| < 4 | Reach |
| < 10 | Recruitable |
| < 18 | Priority Recruit |
| < 35 | Top Recruit |
| < 50 | Conference Star |
| ≥ 50 | High-Point Contender |

### Event Rank (`/api/rank-events`)

Rates each event against USA Swimming 17-18 SCY standards from `usa_motivational_times_17_18_scy.json`. Returns a percentile-style A-score (0.0–1.0) per event, used to surface the swimmer's strongest events first.

---

## Admission Model

### Matrix Model (v2)

**Step 1 — School selectivity tier** (from accept% + satMedian):
- `ultra_selective` | `highly_selective` | `selective` | `broader_admit`

**Step 2 — Academic band (0–4)**
```
sat25 = satMedian - 60
sat75 = satMedian + 60
sat_floor = sat25 - (60|80|100|120 by selectivity tier)

SAT subscore: > sat75 → +2 | sat25–sat75 → +1 | floor–sat25 → 0 | below floor → -2
GPA hard stop: GPA < 2.0 → band 0
raw score → band: 4 → 4, 2–3 → 3, 0–1 → 2, -1 to -2 → 1, ≤ -3 → 0
```

**Step 3 — Swim support band (0–4)**
```
High-Point Contender / Conference Star → 4
Priority Recruit / Top Recruit         → 3
Recruitable                            → 2
Reach                                  → 1
Moonshot                               → 0

PSF modifier: PSF > 1.00 → +1 | PSF ≤ 0.78 → -1 | else 0 (clamped 0–4)
```

**Step 4 — 5×5 matrix lookup → base label → guardrails**

**Labels (6):** Very Strong Chance, Strong Chance, Realistic Shot, Possible, Major Reach, Moonshot

**Special overrides:**
- MIT and Caltech always return `"Moonshot — Apply for Fun"` unconditionally

---

## Search System

`POST /api/search` — body: `{query, name, gpa, sat, act, times, vibe, otherPrefs}`

### School-Name Resolution (`_resolve_school_names`)

Runs before the AI pipeline. If the query looks like a school name, a 6-pass entity resolver runs:

1. **Acronym alias** → score 1.0 (`_ACRONYM_ALIASES`: ~40 entries — CMU, JHU, WPI, RPI, RIT, CWRU, WashU/WUSTL, NYU, GWU, etc.)
2. **Exact normalized name** → score 1.0
3. **Substring match** (both directions, min 4 chars) → score 0.80–0.85
4. **Prefix-token match** (all query tokens are prefixes of name tokens) → score 0.78 — catches truncations like "Penn State", "Georgia Tech", "Johns Hopkin"
5. **difflib fuzzy** against names → cutoff 0.80 (tight to avoid Worcester/Rochester false friends)
6. **City/state surface fallback** → score 0.65 — only fires when passes 1–5 return nothing; enables "Nashville" → Vanderbilt, "Pittsburgh" → Pitt

Confidence gate: `MIN_CONF = 0.55`

**Resolver dispatch:**
- `len(resolved) == 1` → `direct_match` → original similarity-sort + Claude-picks-5 path
- `len(resolved) >= 2` → multi-match early return: `{answer, schools, directMatch: false, multiMatch: true}` — no AI call
- `len(resolved) == 0` → falls through to generative pipeline

### Generative Pipeline (non-school-name queries)

**Step 1 — Query classification** (`_classify_query_mode`):
- `GUIDED`: "for me", "where should I", "best schools for swimming"
- `CONSTRAINED`: "where I can get in", "near me"
- `OBJECTIVE`: "best colleges in America", "top 10", "US News"
- `EXPLORATORY` (default): broad discovery

**Step 2 — Candidate generation** (Claude):
- Returns 12–18 school name strings + 1–2 sentence `answer`
- GUIDED/CONSTRAINED: passes student context (GPA, SAT, events, vibe) but instructs Claude NOT to filter
- OBJECTIVE/EXPLORATORY: no student context

**Step 3 — Map to universe** (`_map_to_universe`):
Fuzzy-matches LLM names to 76-school dataset via exact normalized → substring → key-token Jaccard ≥ 0.50

**Step 4 — Rank and return** (`_search_rank_score`):
- GUIDED/CONSTRAINED: `adm_s × 0.60 + swim_n × 0.40`
- OBJECTIVE/EXPLORATORY: `swim_n × 0.65 + adm_s × 0.35`

Returns top 6 (up to 12 if user asks for more).

**Fallback:** if candidate mapping collapses to < 3 schools, falls back to `_pre_sort()` (keyword-based top-35 pool).

---

## Deep Dive System

`POST /api/deep-dive` — body: `{school, times, sat, gpa, primaryMajor, secondaryMajor, vibeAnswers, otherPrefs}`

Generates 8–12 section narrative for a single school. Claude `claude-sonnet-4-6`, max 2400 tokens. Prompt file: `prompts/lane4_deep_dive_prompt.txt`.

### Section Order

1. **Bottom Line** — 2–3 sentences, always present
2. **In the Pool** — swim-universe schools only
3. **Coach Interest** — swim-universe schools only
4. **What [School] Is Known For**
5. **If You're Serious About [Major]** — only when major known; 4–5 sentences, specific
6. **More: [Major]** — expandable; 6–8 sentences (class size, internships, labs, outcomes, tradeoffs) — pre-generated, hidden by toggle
7. **Are You Admissible?** — admission comparison table injected by frontend
8. **What It Costs** — exact MONEY DATA figures
9. **Campus Life** — 3–4 sentences, vibe-informed
10. **More: Student Experience** — expandable; 4–6 sentences — only when vibe/free-response signals present

### Prompt Constraints (hard rules)
- No em dashes anywhere
- No brochure language, no generic positivity
- No overpersonalization (never "for a student like you" or "because you said X")
- Swim fit explained exactly once, only in "In the Pool"
- "More:" sections do not repeat the parent section — they go further

`POST /api/deep-dive/academic` — returns only the academic section(s) for a given school + major.

---

## Onboarding Wizard

9-screen flow for unauthenticated users. Authenticated returning users start at screen 1 but skip screen 7 (`OB_STATE.isLoggedIn = true`).

| Screen | Name | Description |
|---|---|---|
| 1 | Hero | Swimmer name input + "Find My Times" → SwimCloud search |
| 2 | Swimmer Match | SwimCloud profile cards; "None of these" → manual entry |
| 3 | Fetching Times | Loading screen with rotating humor messages |
| 4 | Time Validation | Editable SCY times table + gender picker; add/delete rows |
| 5 | Searching Colleges | Loading screen while calling `/api/score-all` (public endpoint) |
| 6 | Match Count Reveal | Animated count reveal before asking for email |
| 7 | Email + Password | Account creation; existing account → auth overlay |
| 8 | Building Results | Loading screen while saving profile |
| 9 | Results | Top 6 lane assignments with tier tags (Strong Fit / Exploring / etc.) |

**Public endpoints** (no auth required): `GET /api/public/swimcloud/search`, `GET /api/public/swimcloud/propose`

---

## Auth System

**User auth** (session-based, PostgreSQL):
- `POST /api/auth/register` — email + password + name
- `POST /api/auth/login` — returns session cookie
- `GET /api/auth/me` — returns `{loggedIn, email, name}`
- Session key: `session['user_email']`
- Decorators: `@login_required`, `@user_admin_required`

**Admin auth** (separate session):
- Admin table: `admins(id, email, password_hash, created_by, created_at)`
- Initial admin bootstrapped from `ADMIN_PASSWORD` env var on first startup (`_bootstrap_initial_admin()`)
- Session key: `session['admin_email']`
- Decorator: `@admin_required`
- `GET /api/admin/me` → `{is_admin, email}` — used by frontend to show/hide admin tab

---

## Admin & Image Curator

**Curator UI:** https://lane4-recruit.replit.app/admin/curate

Conference-by-conference workflow with sidebar school list. Three image sections per school:
- **Campus Hero** — exterior/aerial campus shots
- **Aquatics Facility** — pool photos
- **Student Life** — campus life, students, events

**Winner-aware selection:** first selected image shows "Winner" badge (amber); subsequent show "#2", "#3"; clicking winner deselects it and auto-promotes #2.

### Key Files

| File | Purpose |
|---|---|
| `harvest_candidates.py` | Image search, scoring, category assignment, dedup |
| `static/candidates_manifest.json` | Harvested candidates cache (per school, per category, scored) |
| `static/curated_manifest.json` | Admin selections |
| `static/school_images.json` | Public frontend image data (pushed from curated on save) |
| `static/image_blocklist.json` | Globally banned URLs |
| `static/school_domains.json` | Cached school .edu domains (built from Wikipedia extlinks) |

### Candidate Pool Rules
- `_rescore_and_trim_by_category(candidates, per_cat_limit=24)` — dedupes, sorts best-first, trims to 24 per category
- Target: 16 good candidates per bucket; prefetch skips full buckets
- Conference background preload: selecting a conference triggers server-side background fetch for all schools

---

## Data Model

### SCHOOL_META

76 entries. Fields: `accept%`, `satMedian`, `hiddenIvy`, `stem`, `merit`, `location`, `vibe`, `moonshot`.

Keys match canonical names after `TEAM_NAME_MAP` normalization.

**Hidden Ivy schools (17):** Johns Hopkins, Swarthmore, Vassar, Denison, Kenyon, Oberlin, Williams, Tufts, Amherst, Bates, Hamilton, Bowdoin, Middlebury, Colby, Wesleyan, Pomona-Pitzer, Claremont-Mudd-Scripps

**STEM schools (14+):** Johns Hopkins, Swarthmore, RIT, RPI, Clarkson, Union, Tufts, MIT, USCGA, WPI, Clark, Pomona-Pitzer, CMS, Caltech, Washington (Mo), Carnegie Mellon, Case Western, Rochester (UAA)

**Moonshot schools (2):** MIT, Caltech

### Conferences & Programs

9 conferences, 76 total programs:

| Conference | Count |
|---|---|
| Centennial | 8 |
| Liberty League | 10 |
| MIAC | 6 |
| NCAC | 9 |
| NESCAC | 11 |
| NEWMAC | 7 |
| NWC | 8 |
| SCIAC | 9 |
| UAA | 8 |

### Name Normalization (10 known workbook truncations)

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

All normalizations logged in `NORMALIZATION_LOG` and returned in `/api/meta`.

---

## Data Harvester

`harvester.py` — batch-parses NCAA conference championship PDFs into structured CSV anchor data.

### Files

| File | Purpose |
|---|---|
| `parser_helpers.py` | PDF extraction, event header detection, gender classification, time parsing, column-mode routing |
| `harvester.py` | State machine, EventAccumulator, bundle merging, CSV output |
| `conference_map.json` | Filename → conference name mapping, bundle grouping |
| `input_pdfs/` | Input PDFs (one or more per conference bundle) |
| `output/` | All output CSVs |

### Parse Modes

| Mode | Trigger | Description |
|---|---|---|
| `normal` | default | `pdfplumber.extract_text()` — works for most conferences |
| `multi_column_2` | ODAC, MPSF, PCSC | 2-column extraction with y-coordinate row pairing |
| `multi_column_3` | Patriot | 3-column extraction with fixed pixel boundaries (`_PATRIOT_COL_BOUNDARIES = (196.0, 400.0)`) |

### Tolerance Mechanisms

- **DQ-gap interpolation:** if anchor place *p* is absent but neighbors *p-1* and *p+1* present → linearly interpolate
- **Small-field tolerance:** if all places 1…max present (max ≥ 8, max < target) → substitute max-place time
- **Pending-place:** if swimmer name row has no inline time → hold place, pair with time on next line

### Output CSVs

| File | Description |
|---|---|
| `output/event_anchors.csv` | One row per captured event (place 1/8/16 times in seconds + formatted) |
| `output/event_coverage_report.csv` | Per event per gender per bundle — `Coverage_Status`, field depth |
| `output/review_flags.csv` | Per-event quality notes, time suffix handling, recovery log |
| `output/fallback_usage.csv` | Anchors using prelim or non-finals data |
| `output/event_header_debug.csv` | State-machine trace for every event header seen |
| `output/column_mode_debug.csv` | Per-page column detection log (ODAC/Patriot only) |
| `output/debug_bundle_report.csv` | Raw extraction rows before merge |

### Coverage_Status Values

| Status | Meaning |
|---|---|
| `captured` | All required anchors extracted |
| `missing_optional_1000` | 1000 Free — optional, never penalizes score |
| `missing_likely_not_contested` | Event absent from this conference's meet program |
| `missing_data_ceiling` | Field too shallow for target anchor depth (consecutive_depth < 16) |
| `missing_true_parser_miss` | Should be present and deep enough but parser failed |

### Coverage Results (2026 Championships)

| Conference | Score | Mode | Notes |
|---|---|---|---|
| GLIAC | 28/28 | normal | Perfect |
| ODAC | 26/28 | multi_column_2 | 1000 Free not contested |
| Patriot | 26/28 | multi_column_3 | 1000 Free not contested |
| Summit League | 26/28 | normal | 1000 Free not contested |
| Big East | 26/28 | normal | 1000 Free not contested |
| CCIW | 25/28 | normal | Men 200 Fly: true data ceiling (11 entrants only) |
| MPSF | 26/28 | multi_column_2 | 1000 Free not contested |
| PCSC | 28/28 | multi_column_2 | Perfect |
| WAC | 18/28 | normal | Mixed single/multi-column layout — needs per-page adaptive mode |

---

## Key Design Decisions

- **Workbook overrides spec** wherever they conflict (formulas, PSF values, place-cap at 1.0)
- **1000 Free benchmark** only exists in NESCAC — spec said Liberty League/MIAC/NESCAC/SCIAC but workbook is authoritative
- **UAA names** are intentionally abbreviated (Emory, NYU, Chicago, etc.) — not truncations
- **Schools with rawPts = 0** excluded from score-all results (zero-point exclusion guardrail)
- **No openpyxl dependency** — Excel fully retired; all data is CSV-only at runtime
- **Benchmarks repair:** 37 rows fixed (19 men's + 18 women's 8th_seconds conversion bug; 6 Category B corrupted anchors restored from source PDFs); 2 duplicate rows removed
- **`consecutive_depth`** — unbroken run from place 1; immune to spurious loose-scan bleed-in; authoritative field depth metric
- **CID ligature normalization:** `(cid:976)` → `"f"` in HY-TEK MM8 fonts; resolves "Butter(cid:976)ly" → "Butterfly" for event header detection
- **LAST-time extraction:** `parse_place_and_time` returns the last valid time (≥10 s) per row — correctly picks finals time in "Prelim Time | Finals Time" dual-column rows
- **`_detect_column_splits` fallback pass:** when primary 3-bin gap detection finds nothing, secondary pass searches central 40–60% zone with `gap_len ≥ 2` and 50 px merge tolerance; requires ≥ 15 words each side
