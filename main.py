import os
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
import openpyxl

load_dotenv()

app = Flask(__name__, static_folder='static', static_url_path='')

# ---------------------------------------------------------------------------
# Load dataset from Excel on startup
# ---------------------------------------------------------------------------

EXCEL_PATH = os.path.join(os.path.dirname(__file__), 'data', 'lane4_swim_model.xlsx')

BENCHMARKS = {}   # key: "Conference|Event" -> {first, eighth, sixteenth, sec_per_place}
TEAMS = {}        # key: "Conference|Team"  -> {psf, tier, finish, men_points}
CONFERENCES = {}  # key: conference name   -> sorted list of team name strings

def load_data():
    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)

    # --- Sheet1: BENCHMARKS ---
    ws = wb['Sheet1']
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    col = {h: i + 1 for i, h in enumerate(headers) if h}

    for r in range(2, ws.max_row + 1):
        conf  = ws.cell(r, col['Conference']).value
        event = ws.cell(r, col['Event']).value
        if not conf or not event:
            continue
        key = f"{conf}|{event}"
        BENCHMARKS[key] = {
            'first':         _float(ws.cell(r, col['1st_sec']).value),
            'eighth':        _float(ws.cell(r, col['8th_sec']).value),
            'sixteenth':     _float(ws.cell(r, col['16th_sec']).value),
            'sec_per_place': _float(ws.cell(r, col['Sec_per_place']).value),
        }

    # --- Team_Tiers: TEAMS ---
    ws2 = wb['Team_Tiers']
    h2 = [ws2.cell(1, c).value for c in range(1, ws2.max_column + 1)]
    col2 = {}
    for i, h in enumerate(h2):
        if h and h not in col2:   # first occurrence wins — sheet has duplicate headers
            col2[h] = i + 1

    for r in range(2, ws2.max_row + 1):
        conf   = ws2.cell(r, col2['Conference']).value
        team   = ws2.cell(r, col2['Team']).value
        psf    = ws2.cell(r, col2['PSF']).value
        tier   = ws2.cell(r, col2['Tier']).value
        finish = ws2.cell(r, col2['Finish']).value
        pts    = ws2.cell(r, col2['MenPoints']).value
        if not conf or not team:
            continue
        key = f"{conf}|{team}"
        TEAMS[key] = {
            'psf':        _float(psf) if psf is not None else 1.0,
            'tier':       tier or '',
            'finish':     finish,
            'men_points': _float(pts),
        }
        if conf not in CONFERENCES:
            CONFERENCES[conf] = []
        if team not in CONFERENCES[conf]:
            CONFERENCES[conf].append(team)

    # Sort teams within each conference by finish position
    for conf in CONFERENCES:
        CONFERENCES[conf].sort(
            key=lambda t: TEAMS.get(f"{conf}|{t}", {}).get('finish') or 99
        )

def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

load_data()

# ---------------------------------------------------------------------------
# Scoring engine (authoritative: Swimmer_Calcs formulas from workbook)
# ---------------------------------------------------------------------------

ALL_EVENTS = [
    '50 Free', '100 Free', '200 Free', '500 Free',
    '1000 Free', '1650 Free',
    '100 Back', '200 Back',
    '100 Breast', '200 Breast',
    '100 Fly', '200 Fly',
    '200 IM', '400 IM',
    '50 Breast (Relay Split)',
]

def parse_time(s):
    """Convert time string 'M:SS.ss' or 'SS.ss' to decimal seconds. Returns None if invalid."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        if ':' in s:
            parts = s.split(':', 1)
            return float(parts[0]) * 60 + float(parts[1])
        return float(s)
    except ValueError:
        return None

def estimate_place(sec, b):
    """
    3-zone linear interpolation — matches Swimmer_Calcs formula exactly:
      IF(C<=D, 1,
        IF(C<=E, 1+(C-D)/(E-D)*7,
          IF(C<=F, 8+(C-E)/(F-E)*8,
                   16+(C-F)/G)))
    """
    first     = b['first']
    eighth    = b['eighth']
    sixteenth = b['sixteenth']
    spp       = b['sec_per_place'] or 1.0

    if sec <= first:
        return 1.0                                                      # faster than champion — cap at 1
    elif sec <= eighth:
        denom = (eighth - first) if eighth != first else 1.0
        return 1.0 + (sec - first) / denom * 7                         # zone 1-8
    elif sec <= sixteenth:
        denom = (sixteenth - eighth) if sixteenth != eighth else 1.0
        return 8.0 + (sec - eighth) / denom * 8                        # zone 8-16
    else:
        return 16.0 + (sec - sixteenth) / spp                          # beyond 16th

def exp_points(place):
    """MAX(0, MIN(20, 21 - place)) — from Swimmer_Calcs."""
    return max(0.0, min(20.0, 21.0 - place))

def confidence_weight(place):
    """Confidence multiplier from Swimmer_Calcs bubble-weighting."""
    if place <= 12:
        return 1.0
    elif place <= 14:
        return 0.85
    elif place <= 16:
        return 0.65
    else:
        return 0.0

def place_label(place):
    if place <= 1.5:  return '🥇 Winner'
    if place <= 3.5:  return '🏅 Podium'
    if place <= 8.5:  return 'A Final'
    if place <= 16.5: return 'B Final'
    if place <= 20:   return 'Bubble'
    return 'Out of range'

def tier_label(pts):
    if pts < 1:   return 'Moonshot'
    if pts < 4:   return 'Reach'
    if pts < 10:  return 'Recruitable'
    if pts < 18:  return 'Priority Recruit'
    if pts < 35:  return 'Likely Commit'
    if pts < 50:  return 'Conference Star'
    return 'High-Point Contender'

def score_swimmer(times_input, conference, team):
    """
    Run full scoring pipeline for one swimmer at one school.

    times_input: dict {event_name: time_string}
    conference:  string
    team:        string (must match Team_Tiers exactly)

    Returns structured result dict.
    """
    team_key = f"{conference}|{team}"
    team_data = TEAMS.get(team_key)
    psf = team_data['psf'] if team_data else 1.0

    event_results = []

    for event, time_str in times_input.items():
        sec = parse_time(time_str)
        if sec is None:
            continue

        bench_key = f"{conference}|{event}"
        bench = BENCHMARKS.get(bench_key)
        if bench is None:
            # Event not benchmarked in this conference — skip
            event_results.append({
                'event':      event,
                'time':       time_str,
                'sec':        sec,
                'place':      None,
                'place_label': None,
                'exp_points': None,
                'confidence': None,
                'adj_points': None,
                'benchmarked': False,
            })
            continue

        place = estimate_place(sec, bench)
        ep    = exp_points(place)
        conf  = confidence_weight(place)
        ap    = ep * conf

        event_results.append({
            'event':       event,
            'time':        time_str,
            'sec':         round(sec, 3),
            'place':       round(place, 2),
            'place_label': place_label(place),
            'exp_points':  round(ep, 2),
            'confidence':  conf,
            'adj_points':  round(ap, 2),
            'benchmarked': True,
        })

    # Sort scored events by adj_points descending
    scored = [e for e in event_results if e['benchmarked'] and e['adj_points'] is not None]
    unscored = [e for e in event_results if not e['benchmarked']]
    scored.sort(key=lambda e: e['adj_points'], reverse=True)

    top3 = scored[:3]
    raw_pts = sum(e['adj_points'] for e in top3)
    adj_pts = raw_pts * psf

    return {
        'conference': conference,
        'team':       team,
        'psf':        psf,
        'tier':       team_data['tier'] if team_data else '',
        'events':     scored + unscored,
        'top3':       [e['event'] for e in top3],
        'raw_pts':    round(raw_pts, 2),
        'adj_pts':    round(adj_pts, 2),
        'swim_tier':  tier_label(adj_pts),
    }

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/meta', methods=['GET'])
def meta():
    """Return all conferences and their teams for the UI dropdowns."""
    return jsonify({
        'conferences': sorted(CONFERENCES.keys()),
        'teams':       CONFERENCES,
        'events':      ALL_EVENTS,
    })

@app.route('/api/score', methods=['POST'])
def score():
    """Score a swimmer's times at a specific school/conference."""
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    conference = data.get('conference', '').strip()
    team       = data.get('team', '').strip()
    times      = data.get('times', {})

    if not conference:
        return jsonify({'error': 'Conference is required'}), 400
    if not team:
        return jsonify({'error': 'Team is required'}), 400
    if not times or not any(v and str(v).strip() for v in times.values()):
        return jsonify({'error': 'At least one event time is required'}), 400

    result = score_swimmer(times, conference, team)
    return jsonify(result)

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'benchmarks': len(BENCHMARKS), 'teams': len(TEAMS)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
