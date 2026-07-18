#!/usr/bin/env bash
# Chat test suite runner.
#
# Runs every test_*.py in this tests/ dir with python3, from the emf-new
# repo root, printing a PASS/FAIL line per test. Exits non-zero if any fails.
set -u

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# tests/ -> chat/ -> emf-new/
REPO_ROOT="$(cd "$TESTS_DIR/../.." && pwd)"

PY="${PYTHON:-python3}"

ALL_TESTS=()
for f in "$TESTS_DIR"/test_*.py; do
    [ -e "$f" ] && ALL_TESTS+=("$(basename "$f")")
done

pass=0
fail=0
skip=0
failed_names=()

echo "Chat test suite"
echo "repo root: $REPO_ROOT"
echo "python:    $($PY --version 2>&1)"
echo "------------------------------------------------------------"

for t in "${ALL_TESTS[@]}"; do
    path="$TESTS_DIR/$t"
    if [ ! -f "$path" ]; then
        printf 'SKIP  %-28s (not present)\n' "$t"
        skip=$((skip + 1))
        continue
    fi
    if out="$(cd "$REPO_ROOT" && "$PY" "$path" 2>&1)"; then
        printf 'PASS  %s\n' "$t"
        pass=$((pass + 1))
    else
        printf 'FAIL  %s\n' "$t"
        echo "$out" | sed 's/^/      | /'
        fail=$((fail + 1))
        failed_names+=("$t")
    fi
done

echo "------------------------------------------------------------"
echo "passed=$pass failed=$fail skipped=$skip"

if [ "$fail" -ne 0 ]; then
    echo "FAILED: ${failed_names[*]}"
    exit 1
fi
echo "ALL TESTS PASSED"
exit 0