#!/bin/bash
# Test suite for validate-bash.sh hook
# Reads test cases from bash-test-cases.toml
# Exit codes: 0 = all tests pass, 1 = failures
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.claude/hooks/validate-bash.sh"
TEST_CASES="$SCRIPT_DIR/bash-test-cases.toml"

PASS=0
FAIL=0
SKIP=0
ERRORS=()

# Colors for output (if terminal supports it)
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[0;33m'
    NC='\033[0m' # No Color
else
    GREEN=''
    RED=''
    YELLOW=''
    NC=''
fi

# Check dependencies
check_dependencies() {
    local missing=()

    if ! command -v jq &>/dev/null; then
        missing+=("jq")
    fi

    if ! command -v python3 &>/dev/null; then
        missing+=("python3")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "Error: Missing required dependencies: ${missing[*]}"
        echo "Install with: sudo apt install ${missing[*]}"
        exit 1
    fi

    # Check for tomllib (Python 3.11+) or tomli
    if ! python3 -c "import tomllib" 2>/dev/null && ! python3 -c "import tomli" 2>/dev/null; then
        echo "Error: Python TOML parser required (tomllib in Python 3.11+ or 'pip install tomli')"
        exit 1
    fi
}

# Run a test case
# Args: command expected_decision description
test_command() {
    local cmd="$1"
    local expected="$2"
    local desc="$3"

    local result decision

    # Run the hook with JSON input
    # cwd is set so logs go to 'test-validate-bash.log' rather than 'unknown.log'
    result=$(echo "{\"tool_input\": {\"command\": $(printf '%s' "$cmd" | jq -Rs .)}, \"cwd\": \"$SCRIPT_DIR\"}" | bash "$HOOK" 2>/dev/null) || true

    if [[ -z "$result" ]]; then
        decision="allow"
    else
        decision=$(echo "$result" | jq -r '.hookSpecificOutput.permissionDecision // "error"')
    fi

    if [[ "$decision" == "$expected" ]]; then
        echo -e "  ${GREEN}✓${NC} $desc"
        PASS=$((PASS + 1))
        return 0
    else
        echo -e "  ${RED}✗${NC} $desc"
        echo "    Command: $cmd"
        echo "    Expected: $expected, Got: $decision"
        ERRORS+=("$desc: expected $expected, got $decision")
        FAIL=$((FAIL + 1))
        return 1
    fi
}

# Parse TOML and run tests for a category
run_category_tests() {
    local category="$1"
    local expected="$2"

    echo "Testing: $category (expecting: $expected)"

    # Use Python to parse TOML and output test cases as JSON
    # Filters out cases whose 'os' field doesn't match the current platform
    local tests
    tests=$(python3 -c "
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib

with open('$TEST_CASES', 'rb') as f:
    data = tomllib.load(f)

# Detect OS (same logic as validate-bash.py)
platform = sys.platform
if platform.startswith('linux'):
    current_os = 'linux'
elif platform == 'darwin':
    current_os = 'darwin'
elif platform in ('win32', 'cygwin', 'msys'):
    current_os = 'windows'
else:
    current_os = platform

import json
cases = data.get('$category', [])
for case in cases:
    case_os = case.get('os')
    if case_os is not None and case_os != current_os:
        continue
    print(json.dumps(case))
" 2>/dev/null)

    if [[ -z "$tests" ]]; then
        echo -e "  ${YELLOW}(no test cases)${NC}"
        return
    fi

    while IFS= read -r test_json; do
        local cmd desc
        cmd=$(echo "$test_json" | jq -r '.command')
        desc=$(echo "$test_json" | jq -r '.description')
        test_command "$cmd" "$expected" "$desc" || true
    done <<< "$tests"

    echo
}

# Run inline tests for patterns not easily expressed in TOML
run_inline_tests() {
    echo "Testing: Edge Cases"

    # Variable assignments
    test_command "export FOO=bar" "allow" "Export statement" || true

    # Complex but safe pipelines
    test_command "ps aux | grep nginx | awk '{print \$2}'" "allow" "Complex safe pipeline" || true

    # Note: Heredocs with && inside are a known limitation - they trigger ask
    # because the validator sees && in the string. This is safer (false positive)
    # than missing a real dangerous command (false negative).

    echo
}

# Main
main() {
    echo "=== Bash Hook Test Suite ==="
    echo "Test cases: $TEST_CASES"
    echo

    check_dependencies

    if [[ ! -f "$TEST_CASES" ]]; then
        echo "Error: Test cases file not found: $TEST_CASES"
        exit 1
    fi

    if [[ ! -f "$HOOK" ]]; then
        echo "Error: Hook script not found: $HOOK"
        exit 1
    fi

    # Run tests for each category
    run_category_tests "allow" "allow"
    run_category_tests "ask" "ask"
    run_category_tests "deny" "deny"

    # Run inline edge case tests
    run_inline_tests

    # Summary
    echo "=== Summary ==="
    echo -e "Passed: ${GREEN}$PASS${NC}"
    echo -e "Failed: ${RED}$FAIL${NC}"

    if [[ $FAIL -gt 0 ]]; then
        echo
        echo "Failures:"
        for err in "${ERRORS[@]}"; do
            echo "  - $err"
        done
        exit 1
    fi

    echo -e "\n${GREEN}All tests passed!${NC}"
    exit 0
}

# Allow running specific categories via command line
if [[ $# -gt 0 ]]; then
    case "$1" in
        allow|ask|deny)
            check_dependencies
            run_category_tests "$1" "$1"
            echo "Passed: $PASS, Failed: $FAIL"
            [[ $FAIL -eq 0 ]] && exit 0 || exit 1
            ;;
        --help|-h)
            echo "Usage: $0 [allow|ask|deny]"
            echo "  Run all tests or specific category"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [allow|ask|deny]"
            exit 1
            ;;
    esac
else
    main
fi
