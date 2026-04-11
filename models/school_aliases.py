# School name alias tables extracted from main.py

# ── 2026 snapshot tier enrichment ────────────────────────────────────────────
# Abbreviated Excel names → full snapshot names (UAA uses short names in the Excel)
_UAA_SHORT = {
    'emory':          'emory university',
    'nyu':            'new york university',
    'chicago':        'university of chicago',
    'washingtonmo':   'washington university st louis',
    'carnegiemellon': 'carnegie mellon university',
    'casewestern':    'case western reserve universit',
    'rochester':      'university of rochester',
    'brandeis':       'brandeis university',
}

_CAND_STOP = frozenset({
    'university', 'college', 'institute', 'school', 'of', 'the', 'at',
    'and', 'in', 'a', 'tech', 'for',
})

# Full names Claude commonly returns → canonical short names used in our universe.
# Required because the universe stores these as acronyms/short-forms that the
# three-tier fuzzy matcher cannot bridge from the full official name.
_UNIVERSE_ALIASES: dict[str, str] = {
    'massachusetts institute of technology': 'mit',
    'california institute of technology':    'caltech',
    'new york university':                   'nyu',
    'university of chicago':                 'chicago',
    'emory university':                      'emory',
    'virginia tech':                         'va tech',
    'virginia polytechnic institute':        'va tech',
    'virginia polytechnic institute and state university': 'va tech',
    'university of idaho':                   'idaho',
    'seattle university':                    'seattle',
}

# ── School-entity search: acronym/nickname alias table ────────────────────────
# Maps _qnorm(query) → canonical school name(s) stored in the universe.
# ONLY genuine acronyms and nickname contractions that text-surface matching
# fundamentally cannot resolve (pure initials, stored abbreviations, etc.).
# City/substring/prefix-token/difflib passes handle everything else.
_ACRONYM_ALIASES: dict = {
    # UC system — acronyms not deducible from stored canonical names
    'ucsb':              'UC Santa Barbara',
    'ucsd':              'UC San Diego',
    'ucla':              'University of California, Los',
    'ucb':               'California, University of, Ber',
    'uc berkeley':       'California, University of, Ber',
    'ucd':               'UC Davis',
    'uc davis':          'UC Davis',
    # Common school initials (city alone can't bridge these)
    'cmu':               'Carnegie Mellon',
    'jhu':               'Johns Hopkins University',
    'cwru':              'Case Western',
    'wpi':               'Worcester Polytechnic Institute',
    'rpi':               'Rensselaer Polytechnic Institute',
    'rit':               'Rochester Institute of Technology',
    'njit':              'New Jersey Institute of Techno',
    'mit':               'MIT',
    'nyu':               'NYU',
    'gwu':               'George Washington University',
    # Nickname contractions
    'washu':             'Washington (Mo)',
    'wustl':             'Washington (Mo)',
    'wash u':            'Washington (Mo)',
    # Stored-abbreviation mismatches (canonical in universe is a short form)
    'virginia tech':     'VA Tech',
    'vt':                'VA Tech',
    'nc state':          'North Carolina State Universit',
    'ncsu':              'North Carolina State Universit',
    'georgia tech':      'Georgia Institute of Technolog',
    'gatech':            'Georgia Institute of Technolog',
    'gt':                'Georgia Institute of Technolog',
    'odu':               'Old Dominion University',
    'gmu':               'George Mason University',
    'uva':               'Virginia, University of',
    'slu':               'Saint Louis University',
    'unc':               'North Carolina, University of',
    'hws':               'Hobart and William Smith',
    'byu':               'Brigham Young University',
    'lssu':              'Lake Superior State University',
    # Ambiguous multi-candidate entries (explicitly known)
    'rochester':         ['Rochester', 'Rochester Institute of Technology'],
    'augustana':         ['Augustana College', 'Augustana University'],
    'wheaton':           ['Wheaton College', 'Wheaton College (MA)'],
    'idaho':             ['Idaho', 'Idaho, University of', 'College of Idaho'],
    'grand canyon':      ['Grand Canyon', 'Grand Canyon University'],
}

# ── US state abbreviation → full name (for city/state surface matching) ───────
_US_STATES: dict[str, str] = {
    'AL': 'Alabama',        'AK': 'Alaska',         'AZ': 'Arizona',
    'AR': 'Arkansas',       'CA': 'California',      'CO': 'Colorado',
    'CT': 'Connecticut',    'DE': 'Delaware',        'DC': 'District of Columbia',
    'FL': 'Florida',        'GA': 'Georgia',         'HI': 'Hawaii',
    'ID': 'Idaho',          'IL': 'Illinois',        'IN': 'Indiana',
    'IA': 'Iowa',           'KS': 'Kansas',          'KY': 'Kentucky',
    'LA': 'Louisiana',      'ME': 'Maine',           'MD': 'Maryland',
    'MA': 'Massachusetts',  'MI': 'Michigan',        'MN': 'Minnesota',
    'MS': 'Mississippi',    'MO': 'Missouri',        'MT': 'Montana',
    'NE': 'Nebraska',       'NV': 'Nevada',          'NH': 'New Hampshire',
    'NJ': 'New Jersey',     'NM': 'New Mexico',      'NY': 'New York',
    'NC': 'North Carolina', 'ND': 'North Dakota',    'OH': 'Ohio',
    'OK': 'Oklahoma',       'OR': 'Oregon',          'PA': 'Pennsylvania',
    'RI': 'Rhode Island',   'SC': 'South Carolina',  'SD': 'South Dakota',
    'TN': 'Tennessee',      'TX': 'Texas',           'UT': 'Utah',
    'VT': 'Vermont',        'VA': 'Virginia',        'WA': 'Washington',
    'WV': 'West Virginia',  'WI': 'Wisconsin',       'WY': 'Wyoming',
}
