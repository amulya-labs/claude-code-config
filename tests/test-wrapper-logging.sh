#!/bin/bash
# Wrapper-level logging integration tests.
#
# Drives each provider's validate-bash.sh and post-bash.sh wrappers end-to-end
# with sentinel inputs and asserts the expected log lines land in the
# AIDF_HOOK_LOG_DIR. This is the layer that the Codex deny-only-stdout
# regression slipped through — adapter contract tests don't reach it.
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -t 1 ]]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    NC='\033[0m'
else
    GREEN=''
    RED=''
    NC=''
fi

PASS=0
FAIL=0
ERRORS=()

cleanup() {
    if [[ -n "${AIDF_HOOK_LOG_DIR:-}" && -d "$AIDF_HOOK_LOG_DIR" ]]; then
        rm -rf "$AIDF_HOOK_LOG_DIR"
    fi
    return 0
}
trap cleanup EXIT

# Each test gets a fresh tmpdir so log assertions are isolated.
fresh_log_dir() {
    cleanup
    AIDF_HOOK_LOG_DIR=$(mktemp -d -t aidf-wrapper-logs-XXXXXX)
    export AIDF_HOOK_LOG_DIR
    # Skip cleanup-state init churn so logs from this turn are guaranteed to land.
    : > "$AIDF_HOOK_LOG_DIR/.last_cleanup"
    date +%s > "$AIDF_HOOK_LOG_DIR/.last_cleanup"
}

assert_log_contains() {
    local label="$1"
    local needle="$2"
    if grep -RFq "$needle" "$AIDF_HOOK_LOG_DIR" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} ${label}"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}✗${NC} ${label}"
        echo "    Expected log to contain: $needle"
        echo "    Log dir contents:"
        find "$AIDF_HOOK_LOG_DIR" -type f -print -exec sed 's/^/      /' {} \; 2>/dev/null || true
        ERRORS+=("$label: expected '$needle' in logs")
        FAIL=$((FAIL + 1))
    fi
}

assert_log_absent() {
    local label="$1"
    local needle="$2"
    if grep -RFq "$needle" "$AIDF_HOOK_LOG_DIR" 2>/dev/null; then
        echo -e "  ${RED}✗${NC} ${label}"
        echo "    Did not expect: $needle"
        ERRORS+=("$label: unexpected '$needle' in logs")
        FAIL=$((FAIL + 1))
    else
        echo -e "  ${GREEN}✓${NC} ${label}"
        PASS=$((PASS + 1))
    fi
}

run_pre_hook() {
    local provider="$1"
    local payload="$2"
    local hook="$REPO_ROOT/.${provider}/hooks/validate-bash.sh"
    printf '%s' "$payload" | bash "$hook" >/dev/null 2>&1 || true
}

run_post_hook() {
    local provider="$1"
    local payload="$2"
    local hook="$REPO_ROOT/.${provider}/hooks/post-bash.sh"
    printf '%s' "$payload" | bash "$hook" >/dev/null 2>&1 || true
}

# ── Per-provider input fixtures ───────────────────────────────────────────────
# Each provider sees a slightly different stdin shape; embed cwd so the
# project name in the log file stays predictable.

claude_payload() {
    local cmd="$1"
    cat <<EOF
{"hook_event_name":"PreToolUse","tool_name":"Bash","cwd":"$REPO_ROOT","tool_input":{"command":"$cmd"}}
EOF
}

gemini_payload() {
    local cmd="$1"
    cat <<EOF
{"cwd":"$REPO_ROOT","tool_input":{"command":"$cmd","directory":"$REPO_ROOT"}}
EOF
}

codex_payload() {
    local cmd="$1"
    cat <<EOF
{"hook_event_name":"PreToolUse","tool_name":"Bash","cwd":"$REPO_ROOT","tool_input":{"command":"$cmd"}}
EOF
}

# ── Tests ─────────────────────────────────────────────────────────────────────

test_pre_hook_logs_ask() {
    local provider="$1"
    local payload="$2"
    fresh_log_dir
    run_pre_hook "$provider" "$payload"
    assert_log_contains "${provider}: pre-hook logs ASK on git push --force" "ACTION: ASK"
    assert_log_contains "${provider}: pre-hook log carries the command" "git push --force"
}

test_pre_hook_logs_deny() {
    local provider="$1"
    local payload="$2"
    fresh_log_dir
    run_pre_hook "$provider" "$payload"
    assert_log_contains "${provider}: pre-hook logs DENY on sudo rm -rf /" "ACTION: DENY"
}

test_pre_hook_silent_on_allow() {
    local provider="$1"
    local payload="$2"
    fresh_log_dir
    run_pre_hook "$provider" "$payload"
    assert_log_absent "${provider}: pre-hook does not log ALLOW commands" "ACTION:"
}

test_post_hook_logs_approved_for_ask() {
    local provider="$1"
    local payload="$2"
    fresh_log_dir
    run_post_hook "$provider" "$payload"
    assert_log_contains "${provider}: post-hook logs ASK -> APPROVED" "ACTION: ASK -> APPROVED"
}

test_post_hook_silent_on_allow() {
    local provider="$1"
    local payload="$2"
    fresh_log_dir
    run_post_hook "$provider" "$payload"
    assert_log_absent "${provider}: post-hook silent on ALLOW commands" "APPROVED"
}

main() {
    echo "=== Wrapper Logging Test Suite ==="
    echo

    for provider in claude gemini codex; do
        echo "Testing: ${provider} wrapper logging"
        ask_cmd='git push --force'
        deny_cmd='sudo rm -rf /'
        allow_cmd='git status'

        ask_payload=$("${provider}_payload" "$ask_cmd")
        deny_payload=$("${provider}_payload" "$deny_cmd")
        allow_payload=$("${provider}_payload" "$allow_cmd")

        test_pre_hook_logs_ask "$provider" "$ask_payload"
        test_pre_hook_logs_deny "$provider" "$deny_payload"
        test_pre_hook_silent_on_allow "$provider" "$allow_payload"
        test_post_hook_logs_approved_for_ask "$provider" "$ask_payload"
        test_post_hook_silent_on_allow "$provider" "$allow_payload"
        echo
    done

    echo "=== Summary ==="
    echo "Passed: $PASS"
    echo "Failed: $FAIL"
    if (( FAIL > 0 )); then
        echo
        echo "Failures:"
        for err in "${ERRORS[@]}"; do
            echo "  - $err"
        done
        exit 1
    fi
    echo
    echo "All wrapper-logging tests passed!"
}

main "$@"
