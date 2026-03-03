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

EXPECTED_WORKFLOWS=(claude.yml claude-code-review.yml gemini-code-review.yml)

for f in "${EXPECTED_WORKFLOWS[@]}"; do
    if [[ -f "$REPO_ROOT/.github/workflows/$f" ]]; then
        assert ".github/workflows/$f exists" "pass"
    else
        assert ".github/workflows/$f exists" "fail" "File not found"
    fi
done

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

if grep -q 'PROVIDER_CLAUDE_SECRET=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_CLAUDE_SECRET is defined" "pass"
else
    assert "PROVIDER_CLAUDE_SECRET is defined" "fail" \
        "Expected PROVIDER_CLAUDE_SECRET= in script"
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

if grep -q 'PROVIDER_GEMINI_SECRET=' "$MANAGE_SCRIPT"; then
    assert "PROVIDER_GEMINI_SECRET is defined" "pass"
else
    assert "PROVIDER_GEMINI_SECRET is defined" "fail" \
        "Expected PROVIDER_GEMINI_SECRET= in script"
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

# ── 'gemini' top-level agent ─────────────────────────────────────────

echo "=== 'gemini' top-level agent ==="

if grep -q "^    gemini)" "$MANAGE_SCRIPT" || grep -q "^gemini)" "$MANAGE_SCRIPT"; then
    assert "Agent 'gemini' is handled in main case" "pass"
else
    assert "Agent 'gemini' is handled in main case" "fail" \
        "Expected gemini) case branch in main case statement"
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
