#!/bin/sh
# Run every host test in build/host-tests and print one PASS / FAIL / SKIP line
# per test, then a summary. Exit status is non-zero when a test failed.
#
#   build/host-tests/run-all.sh                 # all tests
#   build/host-tests/run-all.sh check-winsxs    # only the named tests
#
# Tests that need swiftc look for it on PATH or in $SWIFTC. When no Swift
# compiler is found they are reported as SKIP (needs swiftc), not as PASS.
# Tests that need a configured Wine tree (MADEIRA_WINE_BUILD) report
# "SKIP-FAIL" themselves and are counted as SKIP here.
#
# Logs go to ${HOST_TEST_LOGS:-/tmp/madeira-host-tests}/<test>.log.
# Nothing here proves on-device (iPad / Metal) behaviour.
set -u
here=$(cd "$(dirname "$0")" && pwd)
logs=${HOST_TEST_LOGS:-/tmp/madeira-host-tests}
mkdir -p "$logs"

swift_ok=0
if [ -n "${SWIFTC:-}" ] && [ -x "$SWIFTC" ]; then
    swift_ok=1
    PATH=$(dirname "$SWIFTC"):$PATH; export PATH
elif command -v swiftc >/dev/null 2>&1; then
    swift_ok=1
    SWIFTC=$(command -v swiftc); export SWIFTC
fi

if [ $# -gt 0 ]; then
    tests="$*"
else
    tests=$(cd "$here" && ls check-*.py | sed 's/\.py$//')
fi

pass=0; fail=0; skip=0; failed=""; skipped=""
for t in $tests; do
    t=${t%.py}
    f="$here/$t.py"
    log="$logs/$t.log"
    if [ "$swift_ok" = 0 ] && grep -q "swiftc\|'swift'" "$f"; then
        echo "SKIP $t (needs swiftc)"; skip=$((skip+1)); skipped="$skipped $t"; continue
    fi
    if timeout "${HOST_TEST_TIMEOUT:-900}" python3 "$f" >"$log" 2>&1; then
        echo "PASS $t"; pass=$((pass+1))
    elif grep -q '^SKIP-FAIL' "$log"; then
        echo "SKIP $t ($(grep -m1 '^SKIP-FAIL' "$log" | cut -c1-120))"; skip=$((skip+1)); skipped="$skipped $t"
    else
        echo "FAIL $t (log: $log)"; fail=$((fail+1)); failed="$failed $t"
    fi
done
echo "== host tests: $pass passed, $fail failed, $skip skipped"
[ -n "$failed" ] && echo "FAILED:$failed"
[ -n "$skipped" ] && echo "SKIPPED:$skipped"
[ "$fail" = 0 ]
