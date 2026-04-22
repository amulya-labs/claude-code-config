#!/bin/bash
# Gemini CLI AfterTool hook adapter for the shared Bash policy engine.
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_DIR="$(cd "$SCRIPT_DIR/../../.ai-dev-foundry/shared/hooks/bash-policy" && pwd)"
CONFIG_FILE="$SHARED_DIR/bash-patterns.toml"

# shellcheck source=/dev/null
source "$SHARED_DIR/hook-lib.sh"

aidf_init_log_dir
[[ -z "$AIDF_HOOK_LOG_DIR" ]] && exit 0

INPUT=$(cat)
PROJECT=$(aidf_extract_project_from_json_input "$INPUT")
COMMAND=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    tool_input = data.get('tool_input') or data.get('toolInput') or {}
    print((tool_input.get('command') or tool_input.get('commandLine') or '').replace('\n', '\\\\n'))
except Exception:
    print('')
" 2>/dev/null)

[[ -z "$COMMAND" ]] && exit 0

LOG_FILE="$(aidf_log_file_for_project "$PROJECT")"
DECISION=$(printf '%s' "$INPUT" | python3 "$SCRIPT_DIR/validate-bash.py" "$CONFIG_FILE" 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('decision', ''))
except Exception:
    print('')
" 2>/dev/null)

if [[ "$DECISION" == "ask" ]]; then
    {
        echo "========================================"
        echo "TIME:   $(LC_ALL=C date '+%Y-%m-%d %H:%M:%S')"
        echo "ACTION: ASK -> APPROVED"
        echo "CMD:    $(aidf_sanitize_for_log "$COMMAND")"
        echo "========================================"
    } >> "$LOG_FILE"
fi

exit 0
