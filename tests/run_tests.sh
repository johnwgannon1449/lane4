#!/bin/bash
# Run the refactor safety-net tests.
# Uses the venv from the sibling lane4cleanbroken project which has all deps.
VENV_PYTHON="/Users/josephgannon/lane4cleanbroken/.venv/bin/python"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== Smoke Test ==="
PYTHONPATH="" "$VENV_PYTHON" tests/smoke_test.py || exit 1

echo ""
echo "=== Route Inventory Test ==="
PYTHONPATH="" "$VENV_PYTHON" tests/test_routes.py || exit 1

echo ""
echo "All tests passed."
