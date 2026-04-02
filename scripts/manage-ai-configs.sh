#!/bin/bash
set -e

# AI Dev Foundry Config Manager
# Copy this script to your project and use it to install/update AI agent configs
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)
#
# Usage:
#   ./scripts/manage-ai-configs.sh claude install                        # First-time setup (includes Claude workflows)
#   ./scripts/manage-ai-configs.sh claude install --with-gha-workflows   # Also install extra workflow templates
#   ./scripts/manage-ai-configs.sh claude update                         # Pull latest config (includes Claude workflows)
#   ./scripts/manage-ai-configs.sh claude update --with-gha-workflows    # Update including extra workflow templates
#
# Multi-provider usage (comma-separated or individual flags):
#   ./scripts/manage-ai-configs.sh claude install --gemini               # Claude + Gemini workflows
#   ./scripts/manage-ai-configs.sh claude install --ai claude,gemini     # Same as above
#   ./scripts/manage-ai-configs.sh claude update --ai gemini             # Update Gemini workflow only (no Claude .claude/ dir)
#   ./scripts/manage-ai-configs.sh gemini install                        # Gemini workflows only
#   ./scripts/manage-ai-configs.sh all install                           # All providers

REPO="amulya-labs/ai-dev-foundry"
BRANCH="main"
API_BASE="https://api.github.com/repos/$REPO/contents"
RAW_BASE="https://raw.githubusercontent.com/$REPO/$BRANCH"
WITH_GHA_WORKFLOWS=false

# Provider registry — to add a new provider, add entries here only
# (Variables are referenced via indirect expansion; SC2034 is suppressed per-variable)
# shellcheck disable=SC2034
PROVIDER_CLAUDE_WORKFLOWS="claude.yml claude-code-review.yml"
# shellcheck disable=SC2034
PROVIDER_CLAUDE_SECRETS="CLAUDE_CODE_OAUTH_TOKEN"
# shellcheck disable=SC2034
PROVIDER_CLAUDE_LABEL="Claude"
# Optional local config dir — omit if provider has no local config
# shellcheck disable=SC2034
PROVIDER_CLAUDE_CONFIG_DIR=".claude"
# Optional space-separated list of items under the remote config dir to download
# Supported item types: "agents" (dir), "hooks" (dir), "settings.json" (file)
# shellcheck disable=SC2034
PROVIDER_CLAUDE_CONFIG_ITEMS="agents hooks settings.json"

# shellcheck disable=SC2034
PROVIDER_GEMINI_WORKFLOWS="gemini-code-review.yml"
# shellcheck disable=SC2034
PROVIDER_GEMINI_SECRETS="GEMINI_API_KEY"
# shellcheck disable=SC2034
PROVIDER_GEMINI_LABEL="Gemini"
# Space-separated list of scripts under .github/workflows/scripts/ to download
# shellcheck disable=SC2034
PROVIDER_GEMINI_WORKFLOW_SCRIPTS="gemini_review.py gemini_review_workflow.sh"
# Extra files to install alongside workflows (relative to repo root)
# shellcheck disable=SC2034
PROVIDER_GEMINI_EXTRA_FILES=".github/gemini-cache-manifest.yml"
# Gemini has no local config dir yet — leave unset

# shellcheck disable=SC2034
PROVIDER_NOTEBOOKLM_WORKFLOWS="sync-notebooklm.yml"
# shellcheck disable=SC2034
PROVIDER_NOTEBOOKLM_SECRETS="NLM_COOKIES_JSON NLM_NOTEBOOK_ID"
# shellcheck disable=SC2034
PROVIDER_NOTEBOOKLM_LABEL="NotebookLM"
# Extra files to install alongside workflows (relative to repo root)
# shellcheck disable=SC2034
PROVIDER_NOTEBOOKLM_EXTRA_FILES=".github/repomix.config.json"
# NotebookLM has no local config dir — workflow-only

# Populated by flag parsing; positional agent arg also adds entries here
PROVIDERS_ENABLED=()

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}==>${NC} $1"; }
warn() { echo -e "${YELLOW}==>${NC} $1"; }
error() { echo -e "${RED}==>${NC} $1"; exit 1; }

# Check if we're in a git repo
check_git() {
    if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
        error "Not inside a git repository"
    fi

    # Make sure we're at repo root
    cd "$(git rev-parse --show-toplevel)"
}

contains() {
    local needle="$1"; shift
    local item
    for item in "$@"; do [[ "$item" == "$needle" ]] && return 0; done
    return 1
}

# Deduplicate PROVIDERS_ENABLED (preserve order, first occurrence wins)
dedup_providers() {
    local _deduped=()
    local _p
    for _p in "${PROVIDERS_ENABLED[@]}"; do
        if ! contains "$_p" "${_deduped[@]}"; then _deduped+=("$_p"); fi
    done
    PROVIDERS_ENABLED=("${_deduped[@]}")
}

# Fetch list of files from a GitHub directory
get_files_in_dir() {
    local dir="$1"
    local response
    response=$(curl -fsSL "$API_BASE/$dir?ref=$BRANCH" 2>/dev/null) || {
        warn "Failed to fetch file list from $dir"
        return 1
    }

    # Extract filenames from JSON response (works without jq)
    echo "$response" | grep -o '"name": *"[^"]*"' | sed 's/"name": *"\([^"]*\)"/\1/'
}

# Download files from a directory
download_dir() {
    local remote_dir="$1"
    local local_dir="$2"

    mkdir -p "$local_dir"

    info "Fetching $remote_dir..."
    local files
    files=$(get_files_in_dir "$remote_dir") || return 1

    if [ -z "$files" ]; then
        warn "No files found in $remote_dir"
        return 0
    fi

    local success=0
    local failed=0

    while IFS= read -r file; do
        local url="$RAW_BASE/$remote_dir/$file"
        local dest="$local_dir/$file"

        if curl -fsSL "$url" -o "$dest" 2>/dev/null; then
            info "  Downloaded $file"
            ((++success))
        else
            warn "  Failed to download $file"
            ((++failed))
        fi
    done <<< "$files"

    echo "  $success files downloaded"
    if [ $failed -gt 0 ]; then
        warn "  $failed files failed"
    fi
}

download_provider_workflows() {
    local provider="$1"
    local upper
    upper=$(printf '%s' "$provider" | tr '[:lower:]' '[:upper:]')
    local workflows_var="PROVIDER_${upper}_WORKFLOWS"
    local secrets_var="PROVIDER_${upper}_SECRETS"
    local label_var="PROVIDER_${upper}_LABEL"

    if [[ ! -v "$workflows_var" ]]; then
        warn "Unknown provider '$provider' — no registry entry; skipping"
        return 0
    fi

    local label="${!label_var}"
    local secrets="${!secrets_var}"
    local workflows="${!workflows_var}"
    local -a wf_list
    read -ra wf_list <<< "$workflows"

    info "Fetching ${label} GitHub Actions workflows..."
    local workflow_dir=".github/workflows"
    mkdir -p "$workflow_dir"
    for wf in "${wf_list[@]}"; do
        local url="$RAW_BASE/.github/workflows/$wf"
        if curl -fsSL "$url" -o "$workflow_dir/$wf" 2>/dev/null; then
            info "  Downloaded $wf"
        else
            warn "  Failed to download $wf"
        fi
    done

    # Also download workflow helper scripts if defined for this provider
    local scripts_var="PROVIDER_${upper}_WORKFLOW_SCRIPTS"
    if [[ -v "$scripts_var" ]] && [[ -n "${!scripts_var}" ]]; then
        local -a script_list
        read -ra script_list <<< "${!scripts_var}"
        local scripts_dir=".github/workflows/scripts"
        mkdir -p "$scripts_dir"
        info "Fetching ${label} workflow scripts..."
        for script in "${script_list[@]}"; do
            local url="$RAW_BASE/.github/workflows/scripts/$script"
            if curl -fsSL "$url" -o "$scripts_dir/$script" 2>/dev/null; then
                info "  Downloaded $script"
            else
                warn "  Failed to download $script"
            fi
        done
    fi

    # Download extra files if defined for this provider
    local extra_var="PROVIDER_${upper}_EXTRA_FILES"
    if [[ -v "$extra_var" ]] && [[ -n "${!extra_var}" ]]; then
        local -a extra_list
        read -ra extra_list <<< "${!extra_var}"
        info "Fetching ${label} extra files..."
        for extra in "${extra_list[@]}"; do
            local dest_dir
            dest_dir=$(dirname "$extra")
            mkdir -p "$dest_dir"
            local url="$RAW_BASE/$extra"
            if curl -fsSL "$url" -o "$extra" 2>/dev/null; then
                info "  Downloaded $extra"
            else
                warn "  Failed to download $extra"
            fi
        done
    fi

    local -a secrets_list
    read -ra secrets_list <<< "$secrets"
    for secret in "${secrets_list[@]}"; do
        warn "Requires ${secret} secret in your repo settings"
    done
}

# Provider-specific post-download hook for Claude
post_download_claude() {
    local config_dir="$1"
    if [ -d "${config_dir}/hooks" ]; then
        chmod +x "${config_dir}/hooks/"*.sh 2>/dev/null || true
        chmod +x "${config_dir}/hooks/"*.py 2>/dev/null || true
    fi
}

# Merge upstream settings.json into an existing local settings.json.
#
# Strategy:
#   hooks      — replace by (event, matcher) identity; preserve user hooks with different matchers
#   permissions.allow / .deny — set union (dedup by exact string); user entries preserved
#   everything else — user wins; upstream keys added only if absent locally
#
# Requires: jq
merge_settings_json() {
    local upstream_file="$1"
    local local_file="$2"
    local output_file="$3"

    if ! command -v jq &>/dev/null; then
        warn "jq not found — cannot merge settings.json; overwriting with upstream"
        cp "$upstream_file" "$output_file"
        return 0
    fi

    # Validate local JSON; if malformed, back up and overwrite
    if ! jq empty "$local_file" 2>/dev/null; then
        warn "Existing settings.json is malformed JSON — backing up to settings.json.bak"
        cp "$local_file" "${local_file}.bak"
        cp "$upstream_file" "$output_file"
        return 0
    fi

    # Perform the three-tier merge in a single jq invocation
    jq -n --slurpfile local "$local_file" --slurpfile upstream "$upstream_file" '
        ($local[0] // {}) as $l |
        ($upstream[0] // {}) as $u |

        # Merge hooks: for each event in upstream, replace-by-matcher or append
        def merge_hook_event(local_arr; upstream_arr):
            if (local_arr | type) != "array" then upstream_arr
            else
                (upstream_arr | map(.matcher) | map(select(. != null))) as $u_matchers |
                [local_arr[] | select(.matcher as $m | ($u_matchers | index($m)) == null)] +
                upstream_arr
            end;

        (($l.hooks // {}) | keys) as $local_hook_keys |
        (($u.hooks // {}) | keys) as $upstream_hook_keys |
        (($local_hook_keys + $upstream_hook_keys) | unique) as $all_hook_keys |
        (reduce $all_hook_keys[] as $event ({};
            if ($u.hooks[$event] // null) == null then
                . + {($event): $l.hooks[$event]}
            elif ($l.hooks[$event] // null) == null then
                . + {($event): $u.hooks[$event]}
            else
                . + {($event): merge_hook_event($l.hooks[$event]; $u.hooks[$event])}
            end
        )) as $merged_hooks |

        # Merge permissions: union of allow arrays, union of deny arrays
        (($l.permissions // {}) | keys) as $local_perm_keys |
        (($u.permissions // {}) | keys) as $upstream_perm_keys |
        (($local_perm_keys + $upstream_perm_keys) | unique) as $all_perm_keys |
        (reduce $all_perm_keys[] as $key ({};
            ($l.permissions[$key] // null) as $lv |
            ($u.permissions[$key] // null) as $uv |
            if ($lv | type) == "array" and ($uv | type) == "array" then
                . + {($key): (($lv + $uv) | unique)}
            elif $uv != null then
                . + {($key): $uv}
            else
                . + {($key): $lv}
            end
        )) as $merged_perms |

        # Start with upstream+local (new upstream keys adopted; local wins on overlap)
        ($u + $l)
        | if ($merged_hooks | length) > 0 then .hooks = $merged_hooks else . end
        | if ($merged_perms | length) > 0 then .permissions = $merged_perms else . end
    ' > "$output_file"
}

download_provider_config() {
    local provider="$1"
    local upper
    upper=$(printf '%s' "$provider" | tr '[:lower:]' '[:upper:]')
    local config_dir_var="PROVIDER_${upper}_CONFIG_DIR"
    local config_items_var="PROVIDER_${upper}_CONFIG_ITEMS"

    # Provider has no local config dir — nothing to download
    [[ ! -v "$config_dir_var" ]] && return 0

    local config_dir="${!config_dir_var}"
    local config_items="${!config_items_var:-}"
    local label_var="PROVIDER_${upper}_LABEL"
    local label="${!label_var}"

    info "Syncing ${label} local config in ${config_dir}..."
    mkdir -p "$config_dir"

    local item
    local -a _items
    read -ra _items <<< "$config_items"
    for item in "${_items[@]}"; do
        if [[ "$item" == *.* ]]; then
            # Single file (e.g. settings.json)
            info "Fetching ${item}..."
            local tmp_file
            tmp_file=$(mktemp)
            if curl -fsSL "$RAW_BASE/.${provider}/${item}" -o "$tmp_file" 2>/dev/null; then
                if [[ "$item" == "settings.json" ]] && [[ -f "${config_dir}/${item}" ]] && [[ -s "${config_dir}/${item}" ]]; then
                    # Merge upstream settings into existing local settings
                    local merged_file
                    merged_file=$(mktemp)
                    if merge_settings_json "$tmp_file" "${config_dir}/${item}" "$merged_file"; then
                        mv "$merged_file" "${config_dir}/${item}"
                        info "  Merged ${item} (preserved local customizations)"
                    else
                        warn "  Merge failed for ${item} — overwriting with upstream"
                        cp "$tmp_file" "${config_dir}/${item}"
                        rm -f "$merged_file"
                    fi
                else
                    cp "$tmp_file" "${config_dir}/${item}"
                    info "  Downloaded ${item}"
                fi
                rm -f "$tmp_file"
            else
                rm -f "$tmp_file"
                warn "  ${item} not found (optional)"
            fi
        else
            # Directory
            download_dir ".${provider}/${item}" "${config_dir}/${item}"
        fi
    done

    # Provider-specific post-download steps
    local post_fn="post_download_${provider}"
    if declare -f "$post_fn" > /dev/null 2>&1; then
        "$post_fn" "$config_dir"
    fi
}

download_all() {
    for provider in "${PROVIDERS_ENABLED[@]}"; do
        download_provider_config "$provider"
        download_provider_workflows "$provider"
    done

    if $WITH_GHA_WORKFLOWS; then
        download_gha_workflow_templates
    fi
}

download_gha_workflow_templates() {
    info "Fetching additional workflow templates..."
    # TODO: remove this guard when github-workflow-templates/ is populated in the source repo
    warn "No extra workflow templates available yet — coming in a future release"
    # download_dir "github-workflow-templates" ".github/workflows"
}

stage_downloaded_files() {
    local _p _up _cdir_var
    for _p in "${PROVIDERS_ENABLED[@]}"; do
        _up=$(printf '%s' "$_p" | tr '[:lower:]' '[:upper:]')
        _cdir_var="PROVIDER_${_up}_CONFIG_DIR"
        if [[ -v "$_cdir_var" ]]; then
            git add "${!_cdir_var}" 2>/dev/null || true
        fi
        local _extra_var="PROVIDER_${_up}_EXTRA_FILES"
        if [[ -v "$_extra_var" ]] && [[ -n "${!_extra_var}" ]]; then
            local -a _extra_list
            read -ra _extra_list <<< "${!_extra_var}"
            for _ef in "${_extra_list[@]}"; do
                git add "$_ef" 2>/dev/null || true
            done
        fi
    done
    git add .github/workflows/ 2>/dev/null || true
}

print_provider_summary() {
    local verb="$1"
    local _p _up
    for _p in "${PROVIDERS_ENABLED[@]}"; do
        _up=$(printf '%s' "$_p" | tr '[:lower:]' '[:upper:]')
        local label_var="PROVIDER_${_up}_LABEL"
        local secrets_var="PROVIDER_${_up}_SECRETS"
        info "${!label_var} workflows ${verb} in .github/workflows/"
        local -a _secrets_list
        read -ra _secrets_list <<< "${!secrets_var}"
        for _s in "${_secrets_list[@]}"; do
            warn "  → Requires ${_s} secret in your repo settings"
        done
    done
    if $WITH_GHA_WORKFLOWS; then
        info "Extra workflow templates ${verb} to .github/workflows/"
    fi
}

install_config() {
    check_git

    local _p _up _cdir_var _cdir
    for _p in "${PROVIDERS_ENABLED[@]}"; do
        _up=$(printf '%s' "$_p" | tr '[:lower:]' '[:upper:]')
        _cdir_var="PROVIDER_${_up}_CONFIG_DIR"
        if [[ -v "$_cdir_var" ]]; then
            _cdir="${!_cdir_var}"
            if [ -d "$_cdir" ] && [ "$(ls -A "$_cdir" 2>/dev/null)" ]; then
                error "Directory $_cdir already exists and is not empty. Use 'update' instead."
            fi
        fi
    done

    info "Installing AI Dev Foundry config..."
    echo ""
    download_all
    stage_downloaded_files

    echo ""
    info "Done! Config installed."
    print_provider_summary "installed"
    echo ""
    echo "Next steps:"
    echo "  git commit -m 'Add AI Dev Foundry config'"
    echo "  git push"
}

update_config() {
    check_git

    local _p _up _cdir_var _cdir
    for _p in "${PROVIDERS_ENABLED[@]}"; do
        _up=$(printf '%s' "$_p" | tr '[:lower:]' '[:upper:]')
        _cdir_var="PROVIDER_${_up}_CONFIG_DIR"
        if [[ -v "$_cdir_var" ]]; then
            _cdir="${!_cdir_var}"
            if [ ! -d "$_cdir" ]; then
                error "Directory $_cdir not found. Use 'install' first."
            fi
        fi
    done

    info "Updating AI Dev Foundry config..."
    echo ""
    download_all
    stage_downloaded_files

    echo ""
    info "Done! Config updated."
    print_provider_summary "updated"
    echo ""
    echo "Next steps:"
    echo "  git diff --cached  # review changes"
    echo "  git commit -m 'Update AI Dev Foundry config'"
    echo "  git push"
}


usage_claude() {
    echo "Usage: $0 claude <command> [options]"
    echo ""
    echo "Commands:"
    echo "  install   Add .claude config to your project (first-time setup)"
    echo "  update    Pull the latest config (agents, hooks, settings)"
    echo ""
    echo "Options:"
    echo "  --gemini               Also install Gemini PR review workflow"
    echo "  --ai <providers>       Comma-separated provider list (e.g. claude,gemini,notebooklm)"
    echo "  --with-gha-workflows   Also install extra workflow templates from"
    echo "                         github-workflow-templates/ in the source repo"
    echo ""
    echo "This downloads:"
    echo "  .claude/agents/   - Reusable Claude Code agents"
    echo "  .claude/hooks/    - PreToolUse hooks (e.g., bash validation)"
    echo "  .claude/settings.json - Hook configuration"
    echo "  .github/workflows/claude.yml             - @claude mention handler"
    echo "  .github/workflows/claude-code-review.yml - Auto PR review"
    echo "  (requires CLAUDE_CODE_OAUTH_TOKEN secret in repo)"
    echo ""
    echo "With --gemini or --ai claude,gemini, also downloads:"
    echo "  .github/workflows/gemini-code-review.yml          - Gemini PR review (Flash + Pro)"
    echo "  .github/workflows/scripts/gemini_review.py        - Inline review Python helper"
    echo "  (requires GEMINI_API_KEY secret in repo)"
    echo ""
    echo "With --with-gha-workflows, also downloads:"
    echo "  Extra workflow templates from github-workflow-templates/"
}

usage_gemini() {
    echo "Usage: $0 gemini <command> [options]"
    echo ""
    echo "Commands:"
    echo "  install   Add Gemini workflow to your project (first-time setup)"
    echo "  update    Pull the latest Gemini workflow"
    echo ""
    echo "This downloads:"
    echo "  .github/workflows/gemini-code-review.yml          - Gemini PR review (Flash + Pro)"
    echo "  .github/workflows/scripts/gemini_review.py        - Inline review Python helper"
    echo "  (requires GEMINI_API_KEY secret in your repo settings)"
    echo ""
    echo "Options:"
    echo "  --with-gha-workflows   Also install extra workflow templates"
    echo "  --ai <providers>       Comma-separated provider list (accepted; use 'gemini' or 'all' instead)"
    echo "  --gemini               Equivalent to using the 'gemini' subcommand (accepted)"
}

usage_notebooklm() {
    echo "Usage: $0 notebooklm <command> [options]"
    echo ""
    echo "Commands:"
    echo "  install   Add NotebookLM sync workflow to your project (first-time setup)"
    echo "  update    Pull the latest NotebookLM sync workflow"
    echo ""
    echo "This downloads:"
    echo "  .github/workflows/sync-notebooklm.yml  - Automated NotebookLM sync on push to main"
    echo "  .github/repomix.config.json               - Universal ignore patterns for repomix"
    echo "  (requires NLM_COOKIES_JSON and NLM_NOTEBOOK_ID secrets in repo)"
    echo ""
    echo "You must also create .github/notebooklm-sources.yaml defining your source splits."
    echo "See: https://github.com/amulya-labs/ai-dev-foundry/blob/main/examples/notebooklm-sources.yaml"
    echo ""
    echo "Options:"
    echo "  --with-gha-workflows   Also install extra workflow templates"
    echo "  --ai <providers>       Comma-separated provider list"
}

usage_all() {
    echo "Usage: $0 all <command> [options]"
    echo ""
    echo "Commands:"
    echo "  install   Install all provider configs and workflows"
    echo "  update    Update all provider configs and workflows"
    echo ""
    echo "Equivalent to running install/update for every registered provider."
    echo ""
    echo "Registered providers:"
    local _lvar _pname _plower
    while IFS= read -r _lvar; do
        _pname="${_lvar#PROVIDER_}"
        _pname="${_pname%_LABEL}"
        _plower=$(printf '%s' "$_pname" | tr '[:upper:]' '[:lower:]')
        printf "  %-10s %s\n" "$_plower" "${!_lvar}"
    done < <(compgen -v PROVIDER_ | grep '_LABEL$' | sort)
    echo ""
    echo "Options:"
    echo "  --with-gha-workflows   Also install extra workflow templates"
}

usage_main() {
    echo "AI Dev Foundry Config Manager"
    echo ""
    echo "Usage: $0 <provider> <command> [options]"
    echo ""
    echo "Providers:"
    local _lvar _pname _plower _cfg_var _descr
    while IFS= read -r _lvar; do
        _pname="${_lvar#PROVIDER_}"
        _pname="${_pname%_LABEL}"
        _plower=$(printf '%s' "$_pname" | tr '[:upper:]' '[:lower:]')
        _cfg_var="PROVIDER_${_pname}_CONFIG_DIR"
        if [[ -v "$_cfg_var" ]]; then
            _descr="config, hooks, and GitHub Actions workflows"
        else
            _descr="GitHub Actions workflows only"
        fi
        printf "  %-10s %s\n" "$_plower" "${!_lvar} — ${_descr}"
    done < <(compgen -v PROVIDER_ | grep '_LABEL$' | sort)
    printf "  %-10s %s\n" "all" "Install/update all providers at once"
    echo ""
    echo "Commands:"
    echo "  install   First-time setup — downloads config and workflows"
    echo "  update    Pull the latest config from ai-dev-foundry"
    echo ""
    echo "Global options:"
    echo "  --gemini               Also install Gemini workflows (with claude)"
    echo "  --ai <providers>       Comma-separated provider list (e.g. claude,gemini)"
    echo "  --with-gha-workflows   Also install extra workflow templates"
    echo ""
    echo "Quick start:"
    echo "  $0 claude install            # Claude agents, hooks, settings + workflows"
    echo "  $0 gemini install            # Gemini PR review workflow"
    echo "  $0 all install               # All providers"
    echo "  $0 claude install --gemini   # Claude + Gemini"
    echo ""
    echo "Run '$0 <provider>' for provider-specific usage."
}

# --- Main ---

AGENT="${1:-}"
shift || true

# Parse flags from remaining args
_shifted=()
for arg in "$@"; do
    case "$arg" in
        --with-gha-workflows) WITH_GHA_WORKFLOWS=true ;;
        --gemini)             PROVIDERS_ENABLED+=("gemini") ;;
        --ai)
            # --ai requires the next argument; handle below via index tracking
            # We use a sentinel so the next iteration picks up the value
            _shifted+=("$arg")
            ;;
        --ai=*)
            # --ai=claude,gemini style
            _ai_val="${arg#--ai=}"
            IFS=',' read -ra _providers <<< "$_ai_val"
            for _p in "${_providers[@]}"; do
                _upper_p=$(printf '%s' "$_p" | tr '[:lower:]' '[:upper:]')
                _wf_var="PROVIDER_${_upper_p}_WORKFLOWS"
                if [[ -v "$_wf_var" ]]; then
                    PROVIDERS_ENABLED+=("$_p")
                else
                    warn "Unknown provider '$_p' in --ai; ignoring"
                fi
            done
            ;;
        *) _shifted+=("$arg") ;;
    esac
done
set -- "${_shifted[@]+"${_shifted[@]}"}"

# Second pass: handle --ai <value> (space-separated form)
_final=()
_i=0
_args=("$@")
while [ $_i -lt ${#_args[@]} ]; do
    arg="${_args[$_i]}"
    if [ "$arg" = "--ai" ]; then
        _i=$((_i + 1))
        if [ $_i -lt ${#_args[@]} ]; then
            _ai_val="${_args[$_i]}"
            IFS=',' read -ra _providers <<< "$_ai_val"
            for _p in "${_providers[@]}"; do
                _upper_p=$(printf '%s' "$_p" | tr '[:lower:]' '[:upper:]')
                _wf_var="PROVIDER_${_upper_p}_WORKFLOWS"
                if [[ -v "$_wf_var" ]]; then
                    PROVIDERS_ENABLED+=("$_p")
                else
                    warn "Unknown provider '$_p' in --ai; ignoring"
                fi
            done
        else
            error "--ai requires a value (e.g. --ai claude,gemini)"
        fi
    else
        _final+=("$arg")
    fi
    _i=$((_i + 1))
done
set -- "${_final[@]+"${_final[@]}"}"

# Handle a single-provider subcommand: default to that provider if --ai was not given,
# then dispatch to install/update or show usage.
handle_provider_command() {
    local provider="$1"
    local command="${2:-}"
    local usage_func="usage_$provider"

    # If --ai was not given, default to this provider only.
    # If --ai was given, treat it as authoritative.
    if [ ${#PROVIDERS_ENABLED[@]} -eq 0 ]; then
        PROVIDERS_ENABLED=("$provider")
    fi
    dedup_providers

    case "$command" in
        install) install_config ;;
        update)  update_config ;;
        *)       "$usage_func"; exit 1 ;;
    esac
}

case "$AGENT" in
    claude|gemini|notebooklm)
        handle_provider_command "$AGENT" "${1:-}"
        ;;
    all)
        if [ ${#PROVIDERS_ENABLED[@]} -gt 0 ]; then
            warn "'all' with --ai/--gemini: operating on specified providers only, not all"
        else
            _lvar=""
            _pname=""
            while IFS= read -r _lvar; do
                _pname="${_lvar#PROVIDER_}"
                _pname="${_pname%_LABEL}"
                _pname=$(printf '%s' "$_pname" | tr '[:upper:]' '[:lower:]')
                PROVIDERS_ENABLED+=("$_pname")
            done < <(compgen -v PROVIDER_ | grep '_LABEL$' | sort)
        fi
        dedup_providers
        case "${1:-}" in
            install) install_config ;;
            update)  update_config ;;
            *)       usage_all; exit 1 ;;
        esac
        ;;
    *)
        usage_main
        exit 1
        ;;
esac
