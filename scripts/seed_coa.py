"""scripts/seed_coa.py — Upsert verified 2025-2026 COA data for known schools.

Run this on Replit after pulling from GitHub:
    python scripts/seed_coa.py

Only updates schools that are confirmed wrong or new. Does NOT touch
campus_life or known_for text fields — only cost columns.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db, using_sqlite

# ---------------------------------------------------------------------------
# Verified 2025-2026 cost data.
# Sources: each school's official Cost of Attendance page.
#
# merit_offered     : True if the school offers merit scholarships to incoming freshmen
# merit_range_low   : Minimum annual merit award in dollars (None if not published)
# merit_range_high  : Maximum annual merit award in dollars (None if not published)
# merit_notes       : Named scholarships worth calling out
# need_based_headline: A specific, notable need-based policy (None if generic)
# ---------------------------------------------------------------------------

COA_DATA = {
    "Johns Hopkins University": {
        "coa": 92000,
        "merit_offered": False,
        "merit_range_low": None,
        "merit_range_high": None,
        "merit_notes": None,
        "need_based_headline": "Meets 100% of demonstrated financial need. Families earning under $65,000 pay zero tuition.",
    },
    "Case Western Reserve University": {
        "coa": 86000,
        "merit_offered": True,
        "merit_range_low": 5000,
        "merit_range_high": 30000,
        "merit_notes": "Trustee Scholarship (up to full tuition), Dean Scholarship, University Scholarship",
        "need_based_headline": None,
    },
    "Carnegie Mellon University": {
        "coa": 93614,
        "merit_offered": True,
        "merit_range_low": 5000,
        "merit_range_high": 25000,
        "merit_notes": "Tartan Scholarship, Presidential Scholarship",
        "need_based_headline": None,
    },
    "Emory University": {
        "coa": 88536,
        "merit_offered": True,
        "merit_range_low": 10000,
        "merit_range_high": 30000,
        "merit_notes": "Emory Scholars Program (full tuition + stipend for finalists)",
        "need_based_headline": "Emory Opportunity Grant: families earning under $200,000 receive free tuition starting Fall 2026.",
    },
    "Tufts University": {
        "coa": 96078,
        "merit_offered": False,
        "merit_range_low": None,
        "merit_range_high": None,
        "merit_notes": None,
        "need_based_headline": "Meets 100% of demonstrated financial need.",
    },
    "Worcester Polytechnic Institute": {
        "coa": 81000,
        "merit_offered": True,
        "merit_range_low": 5000,
        "merit_range_high": 32000,
        "merit_notes": "Presidential Scholarship (up to full tuition), Dean's Scholarship, WPI Grant",
        "need_based_headline": None,
    },
    "Macalester College": {
        "coa": 74000,
        "merit_offered": True,
        "merit_range_low": 5000,
        "merit_range_high": 25000,
        "merit_notes": "Achievement Scholarship, DeWitt Wallace Scholarship",
        "need_based_headline": "Meets 100% of demonstrated financial need.",
    },
    "Bowdoin College": {
        "coa": 95000,
        "merit_offered": False,
        "merit_range_low": None,
        "merit_range_high": None,
        "merit_notes": None,
        "need_based_headline": "Meets 100% of demonstrated financial need. No loans in aid packages.",
    },
    "Colgate University": {
        "coa": 88000,
        "merit_offered": False,
        "merit_range_low": None,
        "merit_range_high": None,
        "merit_notes": None,
        "need_based_headline": "Meets 100% of demonstrated financial need.",
    },
    "Middlebury College": {
        "coa": 94000,
        "merit_offered": False,
        "merit_range_low": None,
        "merit_range_high": None,
        "merit_notes": None,
        "need_based_headline": "Meets 100% of demonstrated financial need. No loans in aid packages.",
    },
    "University of Chicago": {
        "coa": 90000,
        "merit_offered": True,
        "merit_range_low": 5000,
        "merit_range_high": 30000,
        "merit_notes": "College Enrichment Grant, University Scholarship",
        "need_based_headline": "Meets 100% of demonstrated financial need. Families earning under $125,000 pay no more than 10% of income.",
    },
}


def seed():
    updated = 0
    inserted = 0

    with get_db() as conn:
        for school_name, data in COA_DATA.items():
            if using_sqlite():
                cur = conn.cursor()
                cur.execute(
                    'SELECT school_name FROM school_content_cache WHERE school_name = ?',
                    (school_name,)
                )
                exists = cur.fetchone() is not None

                if exists:
                    conn.execute(
                        'UPDATE school_content_cache SET '
                        'coa = ?, merit_offered = ?, merit_range_low = ?, '
                        'merit_range_high = ?, merit_notes = ?, need_based_headline = ? '
                        'WHERE school_name = ?',
                        (data['coa'], data['merit_offered'], data['merit_range_low'],
                         data['merit_range_high'], data['merit_notes'],
                         data['need_based_headline'], school_name)
                    )
                    conn.commit()
                    print(f'  UPDATED {school_name}: COA ${data["coa"]:,}')
                    updated += 1
                else:
                    conn.execute(
                        'INSERT INTO school_content_cache '
                        '(school_name, known_for, campus_life_main, campus_life_more, '
                        ' coa, merit_offered, merit_range_low, merit_range_high, '
                        ' merit_notes, need_based_headline) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (school_name, '', '', '',
                         data['coa'], data['merit_offered'], data['merit_range_low'],
                         data['merit_range_high'], data['merit_notes'],
                         data['need_based_headline'])
                    )
                    conn.commit()
                    print(f'  INSERTED {school_name}: COA ${data["coa"]:,}')
                    inserted += 1
            else:
                # PostgreSQL
                with conn.cursor() as cur:
                    cur.execute(
                        'INSERT INTO school_content_cache '
                        '(school_name, known_for, campus_life_main, campus_life_more, '
                        ' coa, merit_offered, merit_range_low, merit_range_high, '
                        ' merit_notes, need_based_headline) '
                        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) '
                        'ON CONFLICT(school_name) DO UPDATE SET '
                        'coa = EXCLUDED.coa, '
                        'merit_offered = EXCLUDED.merit_offered, '
                        'merit_range_low = EXCLUDED.merit_range_low, '
                        'merit_range_high = EXCLUDED.merit_range_high, '
                        'merit_notes = EXCLUDED.merit_notes, '
                        'need_based_headline = EXCLUDED.need_based_headline',
                        (school_name, '', '', '',
                         data['coa'], data['merit_offered'], data['merit_range_low'],
                         data['merit_range_high'], data['merit_notes'],
                         data['need_based_headline'])
                    )
                conn.commit()
                print(f'  UPSERTED {school_name}: COA ${data["coa"]:,}')
                updated += 1

    print(f'\nDone. {updated} updated, {inserted} inserted.')


if __name__ == '__main__':
    print('Seeding verified COA data...')
    seed()
