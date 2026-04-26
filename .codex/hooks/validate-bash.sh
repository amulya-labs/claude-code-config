#!/bin/bash
# Codex PreToolUse hook adapter for the shared Bash policy engine.
#
# Thin pass-through: log dir setup, then exec the Python adapter.
# Logging of ask/deny verdicts and Codex stdout shaping both live in
# validate-bash.py — Codex's deny-only stdout contract means there's
# no useful verdict signal for a shell wrapper to parse, so the adapter
# writes log entries itself (see hook_log.py).
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_DIR="$(cd "$SCRIPT_DIR/../../.ai-dev-foundry/shared/hooks/bash-policy" && pwd)"
CONFIG_FILE="$SHARED_DIR/bash-patterns.toml"

# shellcheck source=/dev/null
source "$SHARED_DIR/hook-lib.sh"

aidf_init_log_dir
aidf_cleanup_old_logs
export AIDF_HOOK_LOG_DIR

if [[ ! -f "$CONFIG_FILE" ]]; then
    if [[ -n "$AIDF_HOOK_LOG_DIR" ]]; then
        LOG_FILE="$AIDF_HOOK_LOG_DIR/$(LC_ALL=C date '+%Y-%m-%d')-error.log"
        {
            echo "========================================"
            echo "TIME:   $(LC_ALL=C date '+%Y-%m-%d %H:%M:%S')"
            echo "ERROR:  Configuration file not found"
            echo "PATH:   $CONFIG_FILE"
            echo "========================================"
        } >> "$LOG_FILE"
    fi
    echo "Error: Configuration file not found: $CONFIG_FILE" >&2
    exit 1
fi

INPUT=$(cat)

STDERR_FILE=$(mktemp)
OUTPUT=$(printf '%s' "$INPUT" | python3 "$SCRIPT_DIR/validate-bash.py" "$CONFIG_FILE" 2>"$STDERR_FILE") || {
    PROJECT=$(aidf_extract_project_from_json_input "$INPUT")
    LOG_FILE="$(aidf_log_file_for_project "$PROJECT")"
    COMMAND=$(printf '%s' "$INPUT" | python3 -c "import sys,json; data=json.load(sys.stdin); ti=data.get('tool_input') or data.get('toolInput') or {}; print(ti.get('command') or ti.get('commandLine') or '<unknown>')" 2>/dev/null || echo "<parse error>")
    COMMAND=$(aidf_sanitize_for_log "$COMMAND")
    STDERR_CONTENT=$(cat "$STDERR_FILE" 2>/dev/null || true)
    rm -f "$STDERR_FILE"
    if [[ -n "$LOG_FILE" ]]; then
        {
            echo "========================================"
            echo "TIME:   $(LC_ALL=C date '+%Y-%m-%d %H:%M:%S')"
            echo "ERROR:  Python script failed"
            echo "CMD:    $COMMAND"
            echo "STDERR: $STDERR_CONTENT"
            echo "========================================"
        } >> "$LOG_FILE"
    fi
    exit 1
}
rm -f "$STDERR_FILE"

if [[ -n "$OUTPUT" ]]; then
    printf '%s' "$OUTPUT"
fi
