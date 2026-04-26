#!/bin/bash
# Codex PostToolUse hook adapter for the shared Bash policy engine.
#
# Thin pass-through: ensure log dir is set, then exec the Python adapter.
# All decision and logging logic lives in post-bash.py.
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
export AIDF_HOOK_LOG_DIR

python3 "$SCRIPT_DIR/post-bash.py" "$CONFIG_FILE"
exit 0
