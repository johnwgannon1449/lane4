# Query intent detection extracted from main.py.
import re

def _detect_query_intent(query: str) -> dict:
    """Detect what filtering rules apply to a query.

    Returns:
      is_personal      — query is about THIS swimmer's realistic options.
                         If False the query is objective — return AI's list as-is.
      is_swim          — query is about swimming / being recruited / contributing.
      is_explicit_reach — user explicitly asked for reaches / dream / long-shot
                         schools. When True the swim hard-filter is bypassed even
                         if is_swim and is_personal are both True.
      adm_threshold    — minimum admissions label score required to survive filter.
                         None = no admissions filter (general "for me" queries)
                         80   = Strong Chance or better  ("definitely get in")
                         60   = Realistic Shot or better ("can get in")
    """
    q = query.lower()

    # ── Swim intent ─────────────────────────────────────────────────────────
    # 'recruit' root catches recruited / recruiting / recruitable / recruitment.
    # 'team' catches "teams" (substring). 'fastest'/'fast enough' cover speed
    # queries idiomatic to swim recruiting. 'score' covers swim points queries.
    # 'in range' covers "schools in range for me" (swim-level range).
    is_swim = any(s in q for s in [
        'swim', 'pool', 'stroke', 'relay', 'contribute', 'compete',
        'make the team', 'recruit', 'roster', 'athletic fit',
        'lineup', 'cuts', 'finals', 'time trial',
        'team', 'score', 'in range', 'fast enough', 'fastest',
        # event names
        '50 free', '100 free', '200 free', '500 free', '1000 free', '1650',
        '100 back', '200 back', '100 breast', '200 breast',
        '100 fly', '200 fly', '200 im', '400 im',
        'backstroke', 'breaststroke', 'butterfly', 'individual medley',
        'distance swimmer', 'sprinter',
        # composite swim-fit phrases
        'swim fit', 'event profile', 'distance free',
    ])

    # ── Personal-fit intent ──────────────────────────────────────────────────
    # Covers first-person constructions in both normal and inverted word order
    # (e.g. "I could" AND "could I"), plus fit/chance signal words.
    is_personal = any(s in q for s in [
        # first-person pronouns / possessives
        'for me', 'for my', 'help me', 'find me', 'my list', 'my fit',
        'my shot', 'my chance', 'my best', 'my options', 'my realistic',
        # "I <verb>" constructions
        'i should', 'i can', 'i could', 'i want', 'i need',
        'i have', 'i would', "i'm", 'i am',
        # inverted-order constructions ("where am I", "where could I", "where can I")
        'am i', 'can i', 'could i', 'would i', 'should i',
        # contraction
        "i'd",
        # directive phrases
        'where should i', 'where i', 'recommend', 'find me',
        # fit / chance signals
        'shot at', 'have a shot', 'have a chance', 'a chance at',
        'chance of being', 'realistically', 'realistic', 'able to',
        'good fit', 'best fit', 'right fit', 'fit best', 'in range',
        # possessive / proximity signals
        'my ',          # "my 1650", "my 500 free", "my times", "my chances"
        'like me',      # "distance swimmers like me"
        'recruitable',  # "recruitable schools" implies "schools that recruit me"
        'pipe dream',   # "not pipe dreams" = "realistic for me"
        'swim fit',     # "swim fits" / "swim fit" — inherently personal (fit for me)
        'recruit me',   # "would recruit me", "that would recruit me", "recruit me"
        'want me',      # "schools that would want me"
        'take me',      # "schools that would take me as a swimmer"
    ])

    # ── Explicit-reach override ──────────────────────────────────────────────
    # User intentionally wants schools beyond their realistic level.
    # When True, the swim hard-filter is bypassed so dream/long-shot schools
    # can appear even if the swimmer's recruiting label there is non-competitive.
    is_explicit_reach = any(s in q for s in [
        'dream school', 'reach school', 'long shot', 'longshot',
        'unrealistic', "probably can't", "can't swim", 'stretch school',
        'aspiration', 'long-shot',
        'not fast enough', 'too fast for', 'too slow', "can't make",
        "wouldn't be competitive", 'out of my league',
        # negative-fit / eliminate intent — user is asking about schools to avoid
        'below roster level', 'stop considering', 'no shot', 'have no shot',
        'wasting my time', 'not competitive', 'not in range', 'too far out',
    ])

    # Admissions threshold — only meaningful when is_personal is True.
    # Strong Chance or better (80): explicit certainty language.
    # Realistic Shot or better (60): any "can get in" / admissibility language.
    high_bar = any(s in q for s in [
        'definitely get', 'certain to get', 'guaranteed', 'can definitely',
        'easy to get', 'safety', 'sure thing', 'will get in',
    ])
    std_bar = any(s in q for s in [
        'get in', 'get into', 'admissible', 'realistic',
        'where i can get', 'i could get into', 'can get into', 'i can get in',
    ])

    if high_bar:
        adm_threshold = 80   # Strong Chance or better
    elif std_bar:
        adm_threshold = 60   # Realistic Shot or better
    else:
        adm_threshold = None  # General "for me" — no admissions filter

    # ── Prestige / ceiling sort ───────────────────────────────────────────────
    # When True, viable survivors are re-ranked by academic selectivity
    # (lowest admissions-label score = hardest to get into = first), instead of
    # preserving Claude's ordering or defaulting to adjPts-descending.
    # Only applies when is_personal=True so the swim gate fires first.
    # "hardest" / "strongest academic" / "elite" / "most selective" language.
    is_prestige_sort = is_personal and any(s in q for s in [
        'hardest', 'toughest',
        'most selective', 'most prestigious', 'most impressive',
        'highest ranked', 'highest-ranked', 'best ranked', 'top ranked',
        'strongest academic', 'strongest academics', 'most academically',
        'smartest school', 'smartest schools', 'smartest college',
        'elite school', 'elite schools', 'elite college', 'elite program',
        'most elite', 'most academic', 'academic school', 'academic college',
        'best academic', 'top academic', 'highly selective', 'high academic',
        'most well-known', 'most well known', 'best known',
        'ranked school', 'ranked college', 'ranked university',
    ])

    return {
        'is_personal':       is_personal,
        'is_swim':           is_swim,
        'is_explicit_reach': is_explicit_reach,
        'adm_threshold':     adm_threshold,
        'is_prestige_sort':  is_prestige_sort,
    }


