"""
Route inventory test: verifies that all expected Flask routes are still
registered after each refactor phase.

Run from the lane4/ root:
    python -m pytest tests/test_routes.py -v
or directly:
    python tests/test_routes.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import main as m

# Snapshot of all expected routes (METHOD:path).
# Generated from main.py before any refactoring.
EXPECTED_ROUTES = {
    # Auth
    'POST:/api/auth/register',
    'POST:/api/auth/login',
    'POST:/api/auth/logout',
    'GET:/api/auth/me',
    # Data sync
    'GET:/api/data/load',
    'POST:/api/data/save',
    # SwimCloud (public)
    'GET:/api/public/swimcloud/search',
    'GET:/api/public/swimcloud/propose',
    # SwimCloud (authenticated)
    'GET:/api/swimcloud/search',
    'GET:/api/swimcloud/propose',
    'GET:/api/swimcloud/check-prs',
    # Rankings
    'POST:/api/rank-events',
    # Static pages
    'GET:/',
    'GET:/login',
    'GET:/debug-ui',
    # Scoring / meta
    'GET:/api/meta',
    'GET:/api/schools',
    'GET:/api/school-locations',
    'GET:/api/score-all',
    'POST:/api/score-all',
    # AI search + deep dive
    'POST:/api/search',
    'POST:/api/deep-dive',
    'POST:/api/deep-dive/academic',
    'POST:/api/coach-email',
    # User-admin (ua) routes
    'GET:/admin',
    'GET:/api/ua/schools',
    'GET:/api/ua/candidates/<path:school>',
    'POST:/api/ua/fetch-candidates',
    'POST:/api/ua/fetch-more',
    'POST:/api/ua/redo-section',
    'POST:/api/ua/approve',
    'POST:/api/ua/ban',
    'GET:/api/ua/admins',
    'POST:/api/ua/admins/create',
    # Legacy admin routes
    'GET:/admin/login',
    'POST:/api/admin/login',
    'POST:/api/admin/logout',
    'GET:/api/admin/me',
    'GET:/api/admin/list-admins',
    'POST:/api/admin/create-admin',
    'GET:/admin/curate',
    'GET:/api/admin/conferences',
    'GET:/api/admin/schools',
    'GET:/api/admin/candidates/<path:school>',
    'POST:/api/admin/fetch-candidates',
    'POST:/api/admin/blocklist',
    'POST:/api/admin/prefetch-conference',
    'POST:/api/admin/save',
    'POST:/api/admin/rebuild-school-images',
    # Utility
    'GET:/api/health',
    'GET:/snapshot',
    'GET:/api/resetadmin-lane4-2026',
    'GET:/api/dbcheck',
}


def _registered_routes(app):
    """Return a set of 'METHOD:path' strings for all non-static routes."""
    result = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        for method in rule.methods:
            if method in ('HEAD', 'OPTIONS'):
                continue
            result.add(f'{method}:{rule.rule}')
    return result


def test_route_count():
    registered = _registered_routes(m.app)
    assert len(registered) >= len(EXPECTED_ROUTES), (
        f"Route count dropped: expected >= {len(EXPECTED_ROUTES)}, got {len(registered)}\n"
        f"Missing: {EXPECTED_ROUTES - registered}"
    )
    print(f"  Routes: {len(registered)} registered (expected >= {len(EXPECTED_ROUTES)})")


def test_all_expected_routes_present():
    registered = _registered_routes(m.app)
    missing = EXPECTED_ROUTES - registered
    assert not missing, (
        f"{len(missing)} expected route(s) missing:\n" +
        "\n".join(f"  - {r}" for r in sorted(missing))
    )
    print(f"  All {len(EXPECTED_ROUTES)} expected routes present.")


if __name__ == '__main__':
    tests = [test_route_count, test_all_expected_routes_present]
    failures = []
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failures.append(t.__name__)
    print()
    if failures:
        print(f"FAILED: {len(failures)} test(s)")
        sys.exit(1)
    else:
        print(f"All {len(tests)} route tests passed.")
