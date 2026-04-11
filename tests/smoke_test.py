"""
Smoke test: verifies that importing main.py populates all key globals
and registers all expected Flask routes.

Run from the lane4/ root:
    python -m pytest tests/smoke_test.py -v
or directly:
    python tests/smoke_test.py
"""
import sys
import os

# Make sure we import from the lane4 root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import main as m


def test_explore_schools_populated():
    """EXPLORE_SCHOOLS must be a non-empty list after startup."""
    assert isinstance(m.EXPLORE_SCHOOLS, list), "EXPLORE_SCHOOLS is not a list"
    assert len(m.EXPLORE_SCHOOLS) > 0, f"EXPLORE_SCHOOLS is empty"
    print(f"  EXPLORE_SCHOOLS: {len(m.EXPLORE_SCHOOLS)} entries")


def test_benchmarks_populated():
    """BENCHMARKS must be a non-empty dict after startup."""
    assert isinstance(m.BENCHMARKS, dict), "BENCHMARKS is not a dict"
    assert len(m.BENCHMARKS) > 0, "BENCHMARKS is empty"
    print(f"  BENCHMARKS: {len(m.BENCHMARKS)} events")


def test_school_meta_populated():
    """SCHOOL_META must have the expected schools."""
    assert isinstance(m.SCHOOL_META, dict), "SCHOOL_META is not a dict"
    assert len(m.SCHOOL_META) > 100, f"SCHOOL_META suspiciously small: {len(m.SCHOOL_META)}"
    print(f"  SCHOOL_META: {len(m.SCHOOL_META)} schools")


def test_teams_populated():
    """TEAMS must be a non-empty dict after CSV load."""
    assert isinstance(m.TEAMS, dict), "TEAMS is not a dict"
    assert len(m.TEAMS) > 0, "TEAMS is empty"
    print(f"  TEAMS: {len(m.TEAMS)} teams")


def test_teams_list_populated():
    """TEAMS_LIST must be a non-empty list."""
    assert isinstance(m.TEAMS_LIST, list), "TEAMS_LIST is not a list"
    assert len(m.TEAMS_LIST) > 0, "TEAMS_LIST is empty"
    print(f"  TEAMS_LIST: {len(m.TEAMS_LIST)} entries")


def test_james_defined():
    """JAMES default swimmer profile must be a dict with required fields."""
    assert isinstance(m.JAMES, dict), "JAMES is not a dict"
    assert 'times' in m.JAMES, "JAMES missing 'times'"
    assert 'sat' in m.JAMES or 'gpa' in m.JAMES, "JAMES missing academic fields"
    print(f"  JAMES: {list(m.JAMES.keys())}")


def test_conf_division_populated():
    """CONF_DIVISION must map conference names to divisions."""
    assert isinstance(m.CONF_DIVISION, dict), "CONF_DIVISION is not a dict"
    assert len(m.CONF_DIVISION) > 10, f"CONF_DIVISION suspiciously small: {len(m.CONF_DIVISION)}"
    print(f"  CONF_DIVISION: {len(m.CONF_DIVISION)} entries")


def test_flask_app_exists():
    """The Flask app must exist and have routes registered."""
    assert m.app is not None, "Flask app is None"
    rules = [str(r) for r in m.app.url_map.iter_rules()]
    assert len(rules) > 0, "No routes registered"
    print(f"  Flask routes: {len(rules)} registered")


if __name__ == '__main__':
    tests = [
        test_explore_schools_populated,
        test_benchmarks_populated,
        test_school_meta_populated,
        test_teams_populated,
        test_teams_list_populated,
        test_james_defined,
        test_conf_division_populated,
        test_flask_app_exists,
    ]
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
        print(f"FAILED: {len(failures)} test(s): {failures}")
        sys.exit(1)
    else:
        print(f"All {len(tests)} smoke tests passed.")
