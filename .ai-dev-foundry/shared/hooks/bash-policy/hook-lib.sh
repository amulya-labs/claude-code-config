#!/bin/bash
# Shared shell helpers for AI Dev Foundry hook adapters.
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)

AIDF_HOOK_LOG_DIR="${AIDF_HOOK_LOG_DIR:-/tmp/ai-dev-foundry-hook-logs}"
AIDF_HOOK_LOG_RETENTION_DAYS="${AIDF_HOOK_LOG_RETENTION_DAYS:-15}"
AIDF_HOOK_CLEANUP_INTERVAL_SECONDS="${AIDF_HOOK_CLEANUP_INTERVAL_SECONDS:-86400}"

aidf_init_log_dir() {
    local old_umask
    old_umask=$(umask)
    umask 077
    mkdir -p "$AIDF_HOOK_LOG_DIR" 2>/dev/null
    umask "$old_umask"

    if [[ -L "$AIDF_HOOK_LOG_DIR" ]] || [[ ! -d "$AIDF_HOOK_LOG_DIR" ]] || [[ ! -O "$AIDF_HOOK_LOG_DIR" ]]; then
        AIDF_HOOK_LOG_DIR=""
        return 0
    fi
}

aidf_cleanup_old_logs() {
    [[ -z "$AIDF_HOOK_LOG_DIR" ]] && return 0

    local cleanup_state_file now last_run
    cleanup_state_file="$AIDF_HOOK_LOG_DIR/.last_cleanup"
    now=$(date +%s)
    if [[ -f "$cleanup_state_file" ]]; then
        last_run=$(cat "$cleanup_state_file" 2>/dev/null || echo 0)
    else
        last_run=0
    fi

    if (( now - last_run < AIDF_HOOK_CLEANUP_INTERVAL_SECONDS )); then
        return 0
    fi

    find "$AIDF_HOOK_LOG_DIR" -name "*.log" -type f -mtime +"$AIDF_HOOK_LOG_RETENTION_DAYS" -delete 2>/dev/null || true
    echo "$now" > "$cleanup_state_file" 2>/dev/null || true
}

aidf_sanitize_for_log() {
    printf '%s' "$1" | tr '\n\r\t' ' ' | tr -d '\000-\011\013-\037'
}

aidf_extract_project_from_cwd() {
    local cwd="$1"
    python3 -c '
import os, sys
cwd = sys.argv[1]
if not cwd:
    print("unknown")
elif "/.claude/worktrees/" in cwd:
    before, after = cwd.split("/.claude/worktrees/", 1)
    project = os.path.basename(before)
    agent = after.split("/")[0]
    print(f"{project}-{agent}")
else:
    print(os.path.basename(cwd))
' "$cwd" 2>/dev/null
}

aidf_extract_project_from_json_input() {
    local input="$1"
    printf '%s' "$input" | python3 -c '
import json, os, sys
try:
    data = json.load(sys.stdin)
    cwd = (
        data.get("cwd", "")
        or data.get("tool_input", {}).get("directory", "")
        or data.get("toolInput", {}).get("directory", "")
    )
    if not cwd:
        print("unknown")
    elif "/.claude/worktrees/" in cwd:
        before, after = cwd.split("/.claude/worktrees/", 1)
        print(os.path.basename(before) + "-" + after.split("/", 1)[0])
    else:
        print(os.path.basename(cwd))
except Exception:
    print("unknown")
' 2>/dev/null
}

aidf_extract_project_from_claude_input() {
    aidf_extract_project_from_json_input "$1"
}

aidf_log_file_for_project() {
    local project="$1"
    [[ -z "$AIDF_HOOK_LOG_DIR" ]] && return 0
    printf '%s/%s-%s.log' "$AIDF_HOOK_LOG_DIR" "$(LC_ALL=C date '+%Y-%m-%d-%a')" "$project"
}
