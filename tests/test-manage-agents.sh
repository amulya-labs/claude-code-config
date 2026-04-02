#!/bin/bash
# Tests for manage-ai-configs.sh — verifies workflow templates are distributed
# correctly (sourced from .github/workflows/).
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MANAGE_SCRIPT="$REPO_ROOT/scripts/manage-ai-configs.sh"

PASS=0
FAIL=0
ERRORS=()

if [[ -t 1 ]]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    NC='\033[0m'
else
    GREEN=''
    RED=''
    NC=''
fi

assert() {
    local desc="$1"
    local result="$2"  # "pass" or "fail"
    local detail="${3:-}"

    if [[ "$result" == "pass" ]]; then
        echo -e "  ${GREEN}✓${NC} $desc"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}✗${NC} $desc"
        [[ -n "$detail" ]] && echo "    $detail"
        ERRORS+=("$desc")
        FAIL=$((FAIL + 1))
    fi
}

# ── Repo layout checks ─────────────────────────────────────────────

echo "=== Workflow file locations ==="

EXPECTED_WORKFLOWS=(claude.yml claude-code-review.yml gemini-code-review.yml sync-notebooklm.yml)

for f in "${EXPECTED_WORKFLOWS[@]}"; do
    if [[ -f "$REPO_ROOT/.github/workflows/$f" ]]; then
        assert ".github/workflows/$f exists" "pass"
    else
        assert ".github/workflows/$f exists" "fail" "File not found"
    fi
done

if [[ -f "$REPO_ROOT/.github/workflows/scripts/gemini_review.py" ]]; then
    assert ".github/workflows/scripts/gemini_review.py exists" "pass"
else
    assert ".github/workflows/scripts/gemini_review.py exists" "fail" "File not found"
fi

if [[ -f "$REPO_ROOT/.github/workflows/scripts/gemini_review_workflow.sh" ]]; then
    assert ".github/workflows/scripts/gemini_review_workflow.sh exists" "pass"
else
    assert ".github/workflows/scripts/gemini_review_workflow.sh exists" "fail" "File not found"
fi

if [[ -f "$REPO_ROOT/.github/gemini-cache-manifest.yml" ]]; then
    assert ".github/gemini-cache-manifest.yml exists" "pass"
else
    assert ".github/gemini-cache-manifest.yml exists" "fail" "File not found"
fi

# gha-workflow-templates/ should no longer exist
if [[ ! -d "$REPO_ROOT/gha-workflow-templates" ]]; then
    assert "gha-workflow-templates/ directory removed" "pass"
else
    assert "gha-workflow-templates/ directory removed" "fail" \
        "Directory should be removed — workflows now live in .github/workflows/"
fi

echo

# ── manage-ai-configs.sh script checks ─────────────────────

echo "=== manage-ai-configs.sh flag and function checks ==="

# shellcheck disable=SC2310
if grep -q 'WITH_GHA_WORKFLOWS=false' "$MANAGE_SCRIPT"; then
    assert "Default variable is WITH_GHA_WORKFLOWS" "pass"
else
    assert "Default variable is WITH_GHA_WORKFLOWS" "fail" \
        "Expected WITH_GHA_WORKFLOWS=false in script"
fi

if grep -q -- '--with-gha-workflows)' "$MANAGE_SCRIPT"; then
    assert "Flag --with-gha-workflows is recognized" "pass"
else
    assert "Flag --with-gha-workflows is recognized" "fail"
fi

if ! grep -q -- '--with-workflows)' "$MANAGE_SCRIPT"; then
    assert "Old flag --with-workflows is removed" "pass"
else
    assert "Old flag --with-workflows is removed" "fail" \
        "Found stale --with-workflows reference"
fi

if grep -q 'download_gha_workflow_templates()' "$MANAGE_SCRIPT"; then
    assert "Function download_gha_workflow_templates() exists" "pass"
else
    assert "Function download_gha_workflow_templates() exists" "fail"
fi

if ! grep -q 'download_workflows()' "$MANAGE_SCRIPT"; then
    assert "Old function download_workflows() is removed" "pass"
else
    assert "Old function download_workflows() is removed" "fail" \
        "Found stale download_workflows() definition"
fi

echo

# ── Provider registry ───────────────────────────────────────────────

echo "=== Provider registry ==="

if grep -q 'PROVIDER_CLAUDE_WORKFLOWS=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_CLAUDE_WORKFLOWS is defined" "pass"
else
    assert "PROVIDER_CLAUDE_WORKFLOWS is defined" "fail" \
        "Expected PROVIDER_CLAUDE_WORKFLOWS= in script"
fi

if grep -q 'PROVIDER_CLAUDE_SECRETS=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_CLAUDE_SECRETS is defined" "pass"
else
    assert "PROVIDER_CLAUDE_SECRETS is defined" "fail" \
        "Expected PROVIDER_CLAUDE_SECRETS= in script"
fi

if grep -q 'PROVIDER_CLAUDE_LABEL=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_CLAUDE_LABEL is defined" "pass"
else
    assert "PROVIDER_CLAUDE_LABEL is defined" "fail" \
        "Expected PROVIDER_CLAUDE_LABEL= in script"
fi

if grep -q 'PROVIDER_GEMINI_WORKFLOWS=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_GEMINI_WORKFLOWS is defined" "pass"
else
    assert "PROVIDER_GEMINI_WORKFLOWS is defined" "fail" \
        "Expected PROVIDER_GEMINI_WORKFLOWS= in script"
fi

if grep -q 'PROVIDER_GEMINI_SECRETS=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_GEMINI_SECRETS is defined" "pass"
else
    assert "PROVIDER_GEMINI_SECRETS is defined" "fail" \
        "Expected PROVIDER_GEMINI_SECRETS= in script"
fi

if grep -q 'PROVIDER_GEMINI_LABEL=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_GEMINI_LABEL is defined" "pass"
else
    assert "PROVIDER_GEMINI_LABEL is defined" "fail" \
        "Expected PROVIDER_GEMINI_LABEL= in script"
fi

echo

# ── Generic provider download function ─────────────────────────────

echo "=== Generic provider download function ==="

if grep -q 'download_provider_workflows()' "$MANAGE_SCRIPT"; then
    assert "Function download_provider_workflows() exists" "pass"
else
    assert "Function download_provider_workflows() exists" "fail" \
        "Expected download_provider_workflows() function in script"
fi

if ! grep -q 'download_gha_workflows()' "$MANAGE_SCRIPT"; then
    assert "Function download_gha_workflows() is removed" "pass"
else
    assert "Function download_gha_workflows() is removed" "fail" \
        "download_gha_workflows() should be replaced by download_provider_workflows()"
fi

if ! grep -q 'download_gha_gemini_workflows()' "$MANAGE_SCRIPT"; then
    assert "Function download_gha_gemini_workflows() is removed" "pass"
else
    assert "Function download_gha_gemini_workflows() is removed" "fail" \
        "download_gha_gemini_workflows() should be replaced by download_provider_workflows()"
fi

if grep -q 'for provider in' "$MANAGE_SCRIPT"; then
    assert "Provider loop exists in download_all()" "pass"
else
    assert "Provider loop exists in download_all()" "fail" \
        "Expected 'for provider in' loop in script"
fi

if grep -q 'PROVIDERS_ENABLED=()' "$MANAGE_SCRIPT"; then
    assert "PROVIDERS_ENABLED=() is initialized" "pass"
else
    assert "PROVIDERS_ENABLED=() is initialized" "fail" \
        "Expected PROVIDERS_ENABLED=() initialization in script"
fi

if ! grep -q 'PROVIDER_GEMINI=false' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_GEMINI=false is removed" "pass"
else
    assert "PROVIDER_GEMINI=false is removed" "fail" \
        "PROVIDER_GEMINI=false should be removed in favor of PROVIDERS_ENABLED registry"
fi

echo

# ── download_all() behavior checks ───────────────────────────────────

echo "=== download_all() behavior ==="

# download_gha_workflow_templates should be called conditionally
if grep -B1 'download_gha_workflow_templates' "$MANAGE_SCRIPT" | grep -q 'if \$WITH_GHA_WORKFLOWS'; then
    assert "download_gha_workflow_templates is called conditionally with WITH_GHA_WORKFLOWS" "pass"
else
    assert "download_gha_workflow_templates is called conditionally with WITH_GHA_WORKFLOWS" "fail" \
        "download_gha_workflow_templates should be inside an if \$WITH_GHA_WORKFLOWS block"
fi

if grep -q 'github-workflow-templates' "$MANAGE_SCRIPT"; then
    assert "Script references github-workflow-templates directory" "pass"
else
    assert "Script references github-workflow-templates directory" "fail" \
        "Expected reference to github-workflow-templates in script"
fi

echo

# ── Gemini provider checks ──────────────────────────────────────────

echo "=== Gemini provider support ==="

if grep -q -- '--gemini)' "$MANAGE_SCRIPT"; then
    assert "Flag --gemini is recognized" "pass"
else
    assert "Flag --gemini is recognized" "fail" \
        "Expected --gemini) case in flag parsing"
fi

if grep -q -- '--ai' "$MANAGE_SCRIPT"; then
    assert "Flag --ai is recognized" "pass"
else
    assert "Flag --ai is recognized" "fail" \
        "Expected --ai flag parsing in script"
fi

if grep -q 'gemini-code-review.yml' "$MANAGE_SCRIPT"; then
    assert "Script references gemini-code-review.yml" "pass"
else
    assert "Script references gemini-code-review.yml" "fail" \
        "Expected gemini-code-review.yml in script"
fi

if grep -q 'GEMINI_API_KEY' "$MANAGE_SCRIPT"; then
    assert "Script mentions GEMINI_API_KEY secret" "pass"
else
    assert "Script mentions GEMINI_API_KEY secret" "fail" \
        "Expected GEMINI_API_KEY hint for users in script"
fi

if grep -q 'PROVIDER_GEMINI_WORKFLOW_SCRIPTS=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_GEMINI_WORKFLOW_SCRIPTS is defined" "pass"
else
    assert "PROVIDER_GEMINI_WORKFLOW_SCRIPTS is defined" "fail" \
        "Expected PROVIDER_GEMINI_WORKFLOW_SCRIPTS= in script (gemini_review.py)"
fi

if grep -q 'gemini_review.py' "$MANAGE_SCRIPT"; then
    assert "Script references gemini_review.py" "pass"
else
    assert "Script references gemini_review.py" "fail" \
        "Expected gemini_review.py download in manage-ai-configs.sh"
fi

if grep -q 'gemini_review_workflow.sh' "$MANAGE_SCRIPT"; then
    assert "Script references gemini_review_workflow.sh" "pass"
else
    assert "Script references gemini_review_workflow.sh" "fail" \
        "Expected gemini_review_workflow.sh in PROVIDER_GEMINI_WORKFLOW_SCRIPTS"
fi

if grep -q 'PROVIDER_GEMINI_EXTRA_FILES=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_GEMINI_EXTRA_FILES is defined" "pass"
else
    assert "PROVIDER_GEMINI_EXTRA_FILES is defined" "fail" \
        "Expected PROVIDER_GEMINI_EXTRA_FILES= in script (cache manifest)"
fi

if grep -q 'gemini-cache-manifest.yml' "$MANAGE_SCRIPT"; then
    assert "Script references gemini-cache-manifest.yml" "pass"
else
    assert "Script references gemini-cache-manifest.yml" "fail" \
        "Expected gemini-cache-manifest.yml in PROVIDER_GEMINI_EXTRA_FILES"
fi

echo

# ── NotebookLM provider checks ────────────────────────────────────────

echo "=== NotebookLM provider support ==="

if grep -q 'PROVIDER_NOTEBOOKLM_WORKFLOWS=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_NOTEBOOKLM_WORKFLOWS is defined" "pass"
else
    assert "PROVIDER_NOTEBOOKLM_WORKFLOWS is defined" "fail" \
        "Expected PROVIDER_NOTEBOOKLM_WORKFLOWS= in script"
fi

if grep -q 'PROVIDER_NOTEBOOKLM_SECRETS=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_NOTEBOOKLM_SECRETS is defined" "pass"
else
    assert "PROVIDER_NOTEBOOKLM_SECRETS is defined" "fail" \
        "Expected PROVIDER_NOTEBOOKLM_SECRETS= in script"
fi

if grep -q 'PROVIDER_NOTEBOOKLM_LABEL=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_NOTEBOOKLM_LABEL is defined" "pass"
else
    assert "PROVIDER_NOTEBOOKLM_LABEL is defined" "fail" \
        "Expected PROVIDER_NOTEBOOKLM_LABEL= in script"
fi

if grep -q 'sync-notebooklm.yml' "$MANAGE_SCRIPT"; then
    assert "Script references sync-notebooklm.yml" "pass"
else
    assert "Script references sync-notebooklm.yml" "fail" \
        "Expected sync-notebooklm.yml in script"
fi

if grep -q 'NLM_COOKIES_JSON' "$MANAGE_SCRIPT"; then
    assert "Script mentions NLM_COOKIES_JSON secret" "pass"
else
    assert "Script mentions NLM_COOKIES_JSON secret" "fail" \
        "Expected NLM_COOKIES_JSON in script"
fi

if grep -q 'NLM_NOTEBOOK_ID' "$MANAGE_SCRIPT"; then
    assert "Script mentions NLM_NOTEBOOK_ID secret" "pass"
else
    assert "Script mentions NLM_NOTEBOOK_ID secret" "fail" \
        "Expected NLM_NOTEBOOK_ID in script"
fi

if grep -q 'PROVIDER_NOTEBOOKLM_EXTRA_FILES=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_NOTEBOOKLM_EXTRA_FILES is defined" "pass"
else
    assert "PROVIDER_NOTEBOOKLM_EXTRA_FILES is defined" "fail" \
        "Expected PROVIDER_NOTEBOOKLM_EXTRA_FILES= in script (repomixignore)"
fi

if grep -q 'usage_notebooklm()' "$MANAGE_SCRIPT"; then
    assert "usage_notebooklm() function exists" "pass"
else
    assert "usage_notebooklm() function exists" "fail"
fi

if grep -qE '^\s*(notebooklm\)|.*\|notebooklm[\|)])' "$MANAGE_SCRIPT"; then
    assert "Agent 'notebooklm' is handled in main case" "pass"
else
    assert "Agent 'notebooklm' is handled in main case" "fail" \
        "Expected notebooklm case branch in main case statement"
fi

echo

# ── Extra files download support ─────────────────────────────────────

echo "=== Extra files download support ==="

if grep -q 'EXTRA_FILES' "$MANAGE_SCRIPT"; then
    assert "EXTRA_FILES support exists in download_provider_workflows()" "pass"
else
    assert "EXTRA_FILES support exists in download_provider_workflows()" "fail" \
        "Expected EXTRA_FILES handling in script"
fi

if [[ -f "$REPO_ROOT/.github/repomix.config.json" ]]; then
    assert ".github/repomix.config.json exists" "pass"
else
    assert ".github/repomix.config.json exists" "fail" "File not found"
fi

if [[ -f "$REPO_ROOT/examples/notebooklm-sources.yaml" ]]; then
    assert "examples/notebooklm-sources.yaml exists" "pass"
else
    assert "examples/notebooklm-sources.yaml exists" "fail" "File not found"
fi

echo

# ── 'all' keyword support ───────────────────────────────────────────

echo "=== 'all' keyword support ==="

if grep -q "^    all)" "$MANAGE_SCRIPT" || grep -q "^all)" "$MANAGE_SCRIPT"; then
    assert "Agent 'all' is handled in main case" "pass"
else
    assert "Agent 'all' is handled in main case" "fail" \
        "Expected all) case branch in main case statement"
fi

if grep -q 'usage_all()' "$MANAGE_SCRIPT"; then
    assert "usage_all() function exists" "pass"
else
    assert "usage_all() function exists" "fail"
fi

if grep -q 'compgen -v PROVIDER_' "$MANAGE_SCRIPT"; then
    assert "all) uses compgen -v PROVIDER_ for dynamic provider discovery" "pass"
else
    assert "all) uses compgen -v PROVIDER_ for dynamic provider discovery" "fail" \
        "Expected compgen -v PROVIDER_ for registry-based discovery"
fi

echo

# ── handle_provider_command helper ───────────────────────────────────

echo "=== handle_provider_command helper ==="

if grep -q 'handle_provider_command()' "$MANAGE_SCRIPT"; then
    assert "handle_provider_command() helper exists" "pass"
else
    assert "handle_provider_command() helper exists" "fail" \
        "Expected handle_provider_command() function to reduce case duplication"
fi

echo

# ── 'gemini' top-level agent ─────────────────────────────────────────

echo "=== 'gemini' top-level agent ==="

if grep -qE '^\s*(gemini\)|.*\|gemini[\|)])' "$MANAGE_SCRIPT"; then
    assert "Agent 'gemini' is handled in main case" "pass"
else
    assert "Agent 'gemini' is handled in main case" "fail" \
        "Expected gemini case branch in main case statement"
fi

if grep -q 'usage_gemini()' "$MANAGE_SCRIPT"; then
    assert "usage_gemini() function exists" "pass"
else
    assert "usage_gemini() function exists" "fail"
fi

echo

# ── Provider registry CONFIG_DIR extension ───────────────────────────

echo "=== Provider registry CONFIG_DIR extension ==="

if grep -q 'PROVIDER_CLAUDE_CONFIG_DIR=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_CLAUDE_CONFIG_DIR is defined" "pass"
else
    assert "PROVIDER_CLAUDE_CONFIG_DIR is defined" "fail" \
        "Expected PROVIDER_CLAUDE_CONFIG_DIR= in script"
fi

if grep -q 'PROVIDER_CLAUDE_CONFIG_ITEMS=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_CLAUDE_CONFIG_ITEMS is defined" "pass"
else
    assert "PROVIDER_CLAUDE_CONFIG_ITEMS is defined" "fail" \
        "Expected PROVIDER_CLAUDE_CONFIG_ITEMS= in script"
fi

if grep -q 'download_provider_config()' "$MANAGE_SCRIPT"; then
    assert "download_provider_config() function exists" "pass"
else
    assert "download_provider_config() function exists" "fail"
fi

if grep -q 'post_download_claude()' "$MANAGE_SCRIPT"; then
    assert "post_download_claude() post-hook exists" "pass"
else
    assert "post_download_claude() post-hook exists" "fail"
fi

echo

# ── usage_main() quality ─────────────────────────────────────────────

echo "=== usage_main() quality ==="

if grep -A40 'usage_main()' "$MANAGE_SCRIPT" | grep -q 'Quick start'; then
    assert "usage_main() includes Quick start section" "pass"
else
    assert "usage_main() includes Quick start section" "fail" \
        "Expected 'Quick start:' in usage_main()"
fi

if grep -A40 'usage_main()' "$MANAGE_SCRIPT" | grep -q 'all install'; then
    assert "usage_main() shows 'all install' example" "pass"
else
    assert "usage_main() shows 'all install' example" "fail"
fi

echo

# ── merge_settings_json function ────────────────────────────────────

echo "=== merge_settings_json function ==="

if grep -q 'merge_settings_json()' "$MANAGE_SCRIPT"; then
    assert "merge_settings_json() function exists" "pass"
else
    assert "merge_settings_json() function exists" "fail"
fi

# Functional merge tests (require jq)
if command -v jq &>/dev/null; then

    # Source the function for testing (extract just the function)
    MERGE_FN=$(sed -n '/^merge_settings_json()/,/^}/p' "$MANAGE_SCRIPT")

    # Helper: run merge and return merged JSON (propagates exit code)
    run_merge() {
        local upstream_file="$1" local_file="$2"
        local output_file rc
        output_file=$(mktemp)
        # Source the function in a subshell with stubs for info/warn
        rc=0
        (
            info() { :; }
            warn() { :; }
            eval "$MERGE_FN"
            merge_settings_json "$upstream_file" "$local_file" "$output_file"
        ) || rc=$?
        cat "$output_file"
        rm -f "$output_file"
        return "$rc"
    }

    # Test 1: User env/model preserved after merge
    _up=$(mktemp); _lo=$(mktemp)
    cat > "$_up" << 'UPSTREAM'
{
  "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "validate.sh"}]}]},
  "permissions": {"allow": ["Read(/tmp/*)", "WebSearch"], "deny": []}
}
UPSTREAM
    cat > "$_lo" << 'LOCAL'
{
  "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "old.sh"}]}]},
  "permissions": {"allow": ["Read(/tmp/*)"], "deny": []},
  "env": {"MY_VAR": "hello"},
  "model": "opus"
}
LOCAL
    _merged=$(run_merge "$_up" "$_lo")
    if echo "$_merged" | jq -e '.env.MY_VAR == "hello" and .model == "opus"' &>/dev/null; then
        assert "Merge preserves user env and model" "pass"
    else
        assert "Merge preserves user env and model" "fail" "Got: $_merged"
    fi
    rm -f "$_up" "$_lo"

    # Test 2: Upstream hook replaces local hook with same matcher
    _up=$(mktemp); _lo=$(mktemp)
    cat > "$_up" << 'UPSTREAM'
{
  "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "new-validate.sh"}]}]}
}
UPSTREAM
    cat > "$_lo" << 'LOCAL'
{
  "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "old-validate.sh"}]}]}
}
LOCAL
    _merged=$(run_merge "$_up" "$_lo")
    if echo "$_merged" | jq -e '.hooks.PreToolUse | length == 1 and .[0].hooks[0].command == "new-validate.sh"' &>/dev/null; then
        assert "Merge replaces hook with same matcher" "pass"
    else
        assert "Merge replaces hook with same matcher" "fail" "Got: $_merged"
    fi
    rm -f "$_up" "$_lo"

    # Test 3: User hooks with different matcher are preserved
    _up=$(mktemp); _lo=$(mktemp)
    cat > "$_up" << 'UPSTREAM'
{
  "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "validate.sh"}]}]}
}
UPSTREAM
    cat > "$_lo" << 'LOCAL'
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "old.sh"}]},
      {"matcher": "Write", "hooks": [{"type": "command", "command": "format.sh"}]}
    ]
  }
}
LOCAL
    _merged=$(run_merge "$_up" "$_lo")
    if echo "$_merged" | jq -e '.hooks.PreToolUse | length == 2' &>/dev/null &&
       echo "$_merged" | jq -e '.hooks.PreToolUse[] | select(.matcher == "Write") | .hooks[0].command == "format.sh"' &>/dev/null &&
       echo "$_merged" | jq -e '.hooks.PreToolUse[] | select(.matcher == "Bash") | .hooks[0].command == "validate.sh"' &>/dev/null; then
        assert "Merge preserves user hooks with different matcher" "pass"
    else
        assert "Merge preserves user hooks with different matcher" "fail" "Got: $_merged"
    fi
    rm -f "$_up" "$_lo"

    # Test 4: Permission arrays are unioned (deduped)
    _up=$(mktemp); _lo=$(mktemp)
    cat > "$_up" << 'UPSTREAM'
{
  "permissions": {"allow": ["Read(/tmp/*)", "WebSearch", "WebFetch"]}
}
UPSTREAM
    cat > "$_lo" << 'LOCAL'
{
  "permissions": {"allow": ["Read(/tmp/*)", "Bash(npm:*)"]}
}
LOCAL
    _merged=$(run_merge "$_up" "$_lo")
    _count=$(echo "$_merged" | jq '.permissions.allow | length')
    if [[ "$_count" == "4" ]]; then
        assert "Merge unions permission arrays without duplicates" "pass"
    else
        assert "Merge unions permission arrays without duplicates" "fail" "Expected 4 entries, got: $_count"
    fi
    rm -f "$_up" "$_lo"

    # Test 5: User-only hook events are preserved
    _up=$(mktemp); _lo=$(mktemp)
    cat > "$_up" << 'UPSTREAM'
{
  "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "validate.sh"}]}]}
}
UPSTREAM
    cat > "$_lo" << 'LOCAL'
{
  "hooks": {
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "old.sh"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "cleanup.sh"}]}]
  }
}
LOCAL
    _merged=$(run_merge "$_up" "$_lo")
    if echo "$_merged" | jq -e '.hooks.Stop[0].hooks[0].command == "cleanup.sh"' &>/dev/null; then
        assert "Merge preserves user-only hook events" "pass"
    else
        assert "Merge preserves user-only hook events" "fail" "Got: $_merged"
    fi
    rm -f "$_up" "$_lo"

    # Test 6: Malformed local JSON triggers backup and overwrite
    _up=$(mktemp); _lo=$(mktemp); _out=$(mktemp)
    cat > "$_up" << 'UPSTREAM'
{"hooks": {}}
UPSTREAM
    echo "not valid json {{{" > "$_lo"
    (
        info() { :; }
        warn() { :; }
        eval "$MERGE_FN"
        merge_settings_json "$_up" "$_lo" "$_out"
    )
    if [[ -f "${_lo}.bak" ]] && jq empty "$_out" 2>/dev/null; then
        assert "Malformed local JSON: backs up and overwrites" "pass"
    else
        assert "Malformed local JSON: backs up and overwrites" "fail"
    fi
    rm -f "$_up" "$_lo" "${_lo}.bak" "$_out"

    # Test 7: New upstream top-level keys are adopted
    _up=$(mktemp); _lo=$(mktemp)
    cat > "$_up" << 'UPSTREAM'
{
  "hooks": {},
  "permissions": {},
  "plugins": ["mcp-server-fetch"]
}
UPSTREAM
    cat > "$_lo" << 'LOCAL'
{
  "hooks": {},
  "permissions": {},
  "model": "opus"
}
LOCAL
    _merged=$(run_merge "$_up" "$_lo")
    if echo "$_merged" | jq -e '.plugins == ["mcp-server-fetch"] and .model == "opus"' &>/dev/null; then
        assert "Merge adopts new upstream top-level keys" "pass"
    else
        assert "Merge adopts new upstream top-level keys" "fail" "Got: $_merged"
    fi
    rm -f "$_up" "$_lo"

    # Test 8: Non-array hook event in local is replaced by upstream
    _up=$(mktemp); _lo=$(mktemp)
    cat > "$_up" << 'UPSTREAM'
{
  "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "validate.sh"}]}]}
}
UPSTREAM
    cat > "$_lo" << 'LOCAL'
{
  "hooks": {"PreToolUse": "not-an-array"}
}
LOCAL
    _merged=$(run_merge "$_up" "$_lo")
    if echo "$_merged" | jq -e '.hooks.PreToolUse | type == "array" and length == 1' &>/dev/null; then
        assert "Merge handles non-array local hook event gracefully" "pass"
    else
        assert "Merge handles non-array local hook event gracefully" "fail" "Got: $_merged"
    fi
    rm -f "$_up" "$_lo"

else
    echo "  (jq not installed — skipping merge functional tests)"
fi

echo

# ── settings.json merge in download_provider_config ────────────────

echo "=== settings.json merge integration ==="

if grep -q 'merge_settings_json' "$MANAGE_SCRIPT"; then
    assert "download_provider_config calls merge_settings_json" "pass"
else
    assert "download_provider_config calls merge_settings_json" "fail"
fi

if grep -q 'Merged.*preserved local customizations' "$MANAGE_SCRIPT"; then
    assert "Merge success message is present" "pass"
else
    assert "Merge success message is present" "fail"
fi

if grep -q 'settings.json.bak' "$MANAGE_SCRIPT"; then
    assert "Backup logic for malformed JSON is present" "pass"
else
    assert "Backup logic for malformed JSON is present" "fail"
fi

echo

# ── shellcheck ──────────────────────────────────────────────────────

echo "=== shellcheck ==="

if command -v shellcheck &>/dev/null; then
    if shellcheck "$MANAGE_SCRIPT" 2>&1; then
        assert "manage-ai-configs.sh passes shellcheck" "pass"
    else
        assert "manage-ai-configs.sh passes shellcheck" "fail"
    fi
else
    echo "  (shellcheck not installed — skipping)"
fi

echo

# ── Summary ─────────────────────────────────────────────────────────

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
