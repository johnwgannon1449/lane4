# Swimmer defaults extracted from main.py

# ---------------------------------------------------------------------------
# JAMES — hardcoded swimmer profile (source of truth for all scoring runs)
# ---------------------------------------------------------------------------
JAMES = {
    "name":             "James",
    "gpa":              4.0,
    "sat":              1460,
    "satProjected":     1500,
    "actScore":         0,
    "apCount":          0,
    "mathSat":          720,
    "mathSatProjected": 760,
    "times": {
        "1650 Free":              "16:06",
        "1000 Free":              "9:30",
        "500 Free":               "4:37",
        "200 Free":               "1:43",
        "400 IM":                 "4:09",
        "200 IM":                 "1:56",
        "100 Breast":             "59.5",
        "50 Breast (Relay Split)": "25.68",
    },
    "vibe": {
        "campus":   "Small and tight-knit — everyone knows everyone",
        "friday":   "Library with 2–3 close friends",
        "academic": "Genuinely want to be well-rounded",
        "compete":  "Love pushing myself inside a team environment",
        "location": None,
        "career":   None,
    },
}


ALL_EVENTS = [
    '50 Free', '100 Free', '200 Free', '500 Free',
    '1000 Free', '1650 Free',
    '100 Back', '200 Back',
    '100 Breast', '200 Breast',
    '100 Fly', '200 Fly',
    '200 IM', '400 IM',
    '50 Breast (Relay Split)',
]
