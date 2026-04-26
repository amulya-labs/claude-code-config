# CLAUDE.md

## Quick Start

- **Python 3.11+** required (for `tomllib`; or `pip install tomli` for older versions)
- **jq** required for shell hook tests
- **shellcheck** required for CI lint checks (`sudo apt install shellcheck`)
- **pytest** required for Python tests (`pip install pytest pyyaml`)
- No install step -- this is a config repo, not a buildable project
- Verify setup: `python3 -c "import tomllib; print('ok')" && jq --version && shellcheck --version`
- Run fast tests: `pytest tests/test_validate_bash.py -v`
- Run full CI-equivalent: see Core Commands below

## Core Commands

```bash
# Fast test loop (data-driven from tests/bash-test-cases.toml, < 1 second)
pytest tests/test_validate_bash.py -v

# Single test by name substring
pytest tests/test_validate_bash.py -v -k "test_name_substring"

# Per-provider adapter contract tests (Claude/Gemini/Codex stdin->stdout shape)
pytest tests/test_adapters.py -v

# Shell integration tests (invokes hook via subprocess)
tests/test-validate-bash.sh

# Single category shell tests
tests/test-validate-bash.sh allow    # or: ask, deny

# Wrapper-level logging tests (drives validate-bash.sh / post-bash.sh end-to-end
# for all three providers and asserts log lines land in AIDF_HOOK_LOG_DIR)
tests/test-wrapper-logging.sh

# manage-agents tests
tests/test-manage-agents.sh

# Lint hook scripts
shellcheck .claude/hooks/*.sh .gemini/hooks/*.sh .codex/hooks/*.sh .ai-dev-foundry/shared/hooks/bash-policy/*.sh

# Validate TOML config syntax
python3 -c "import tomllib; tomllib.load(open('.ai-dev-foundry/shared/hooks/bash-policy/bash-patterns.toml','rb')); print('TOML ok')"

# Validate agent frontmatter
python3 -c "
import yaml
from pathlib import Path
for f in Path('.claude/agents').glob('*.md'):
    parts = f.read_text().split('---', 2)
    fm = yaml.safe_load(parts[1])
    assert all(k in fm for k in ['name','description','source','license']), f'{f.name}: missing fields'
    print(f'  ok: {f.name}')
"

# Full CI-equivalent local check (run before pushing)
shellcheck .claude/hooks/*.sh .gemini/hooks/*.sh .codex/hooks/*.sh .ai-dev-foundry/shared/hooks/bash-policy/*.sh tests/test-wrapper-logging.sh && \
  python3 -c "import tomllib; tomllib.load(open('.ai-dev-foundry/shared/hooks/bash-policy/bash-patterns.toml','rb'))" && \
  pytest tests/test_validate_bash.py tests/test_adapters.py -v && \
  tests/test-validate-bash.sh && \
  tests/test-wrapper-logging.sh && \
  tests/test-manage-agents.sh
```

## Repo Map

```
.claude/agents/          -- agent markdown files with YAML frontmatter; add/edit agents here
.claude/hooks/           -- Claude-specific PreToolUse/PostToolUse adapters
.gemini/hooks/           -- Gemini CLI BeforeTool/AfterTool adapters
.codex/hooks/            -- Codex PreToolUse/PostToolUse adapters
  validate-bash.sh       -- Shell entry point: reads Claude JSON, calls validate-bash.py, logs
  validate-bash.py       -- Claude adapter: normalizes hook input/output for the shared engine
  post-bash.sh           -- Claude PostToolUse adapter: logs ASK->APPROVED outcomes
  validate-bash.sh       -- Gemini shell entry point: reads Gemini JSON, calls validate-bash.py, logs
  validate-bash.py       -- Gemini adapter: normalizes hook input/output for the shared engine
  post-bash.sh           -- Gemini AfterTool adapter: logs ASK->APPROVED outcomes
  validate-bash.sh       -- Codex shell entry point: reads Codex JSON, calls validate-bash.py, logs
  validate-bash.py       -- Codex adapter: normalizes hook input/output for the shared engine
  post-bash.sh           -- Codex PostToolUse adapter: logs ASK->APPROVED outcomes
.ai-dev-foundry/shared/hooks/bash-policy/ -- Provider-neutral Bash policy engine
  validate-command.py    -- Shared validator with normalized JSON contract
  hook-lib.sh            -- Shared shell logging/path helpers
  bash-patterns.toml     -- Universal regex patterns: [deny.*], [ask.*], [allow.*]
  bash-patterns.linux.toml  -- Linux-specific pattern overlay (merged at runtime)
  bash-patterns.darwin.toml -- macOS-specific pattern overlay (merged at runtime)
  bash-patterns.windows.toml -- Windows/Git Bash pattern overlay (merged at runtime)
.claude/settings.json    -- Claude hook wiring + permission allow/deny lists (shared/committed)
.gemini/settings.json    -- Gemini CLI hook wiring (shared/committed)
.codex/hooks.json        -- Codex hook wiring (shared/committed)
.claude/settings.local.json -- Local-only permission overrides (committed but for local use)
scripts/manage-ai-configs.sh -- Install/update AI agent configs and GHA workflows via curl from GitHub
scripts/git-subtree-mgr  -- Git subtree manager for tracking upstream changes
.github/workflows/       -- Live GitHub Actions: ci.yml, scorecard.yml, claude.yml, claude-code-review.yml
tests/                   -- All tests: bash-test-cases.toml, test_validate_bash.py, test-validate-bash.sh, test-manage-agents.sh
.github/workflows/ci.yml -- CI job definitions (source of truth for what must pass)
```

## Change Workflow

- **Never push directly to main** -- always create a branch and open a PR
- **Before pushing**: run the full CI-equivalent check (see Core Commands)

### Agents
- Edit/add `.claude/agents/<name>.md`
- Required frontmatter: `name`, `description`, `source`, `license`
- Optional frontmatter: `model` (opus/sonnet/haiku), `color`
- Body: markdown instructions after the closing `---`
- Must be generalized (no project-specific references), focused (one domain per agent)

### Hook Patterns
- Edit `.ai-dev-foundry/shared/hooks/bash-policy/bash-patterns.toml` for universal (cross-platform) patterns
- Edit `.ai-dev-foundry/shared/hooks/bash-policy/bash-patterns.{linux,darwin,windows}.toml` for OS-specific patterns
- **OS-aware layering**: the validator auto-detects the OS via `sys.platform` and merges the matching OS file on top of the base file. OS patterns are **appended** to matching base sections (never replace). New sections from OS files are added as-is.
- Categories: `[deny.*]` (always block), `[ask.*]` (prompt user), `[allow.*]` (auto-approve)
- Evaluation order: deny -> ask -> allow -> ask (default if no match)
- **Lean liberal**: if a command is safe, add it to allow; reserve ask for genuinely risky ops
- When a chain of commands (`cmd1 && cmd2 && cmd3`) is composed entirely of individually safe commands, the chain should be auto-approved -- never prompt just because commands are chained together
- The goal is minimal friction: agents should rarely be interrupted for routine dev commands (build tools, linters, test runners, package managers, git operations, file manipulation on relative paths)
- For every new pattern, add corresponding test cases to `tests/bash-test-cases.toml`
- **Where to add patterns**: cross-platform commands go in `bash-patterns.toml`; OS-specific commands (e.g. `systemd-analyze` for Linux, `defaults write` for macOS) go in the matching OS file

### Hook Logic
- Edit `.ai-dev-foundry/shared/hooks/bash-policy/validate-command.py` for shared validator behavior
- Edit `.claude/hooks/validate-bash.py`, `.gemini/hooks/validate-bash.py`, or `.codex/hooks/validate-bash.py` only for provider-specific translation
- The shell wrapper (`validate-bash.sh`) handles logging and adapter invocation only; do not add validation logic there
- The validator splits chains (`&&`, `||`, `;`), cleans segments (strips env vars, subshell chars, control flow keywords), then matches each segment against patterns independently
- A chain is allowed only if ALL segments are allowed; any ask/deny segment escalates the whole chain

### Test Cases
- Add entries to `tests/bash-test-cases.toml` as `[[allow]]`, `[[ask]]`, or `[[deny]]`
- Each entry: `command` (string), `description` (string), and optional `os` (string)
- The `os` field filters tests by platform: `"linux"`, `"darwin"`, or `"windows"`. Omit for cross-platform tests.
- Tests are data-driven: both `test_validate_bash.py` and `test-validate-bash.sh` read from this file

### Attribution
- All `.sh`, `.py`, and `.toml` files under `.claude/hooks/`, `.gemini/hooks/`, `.codex/hooks/`, `.ai-dev-foundry/shared/hooks/bash-policy/`, and `scripts/`, and all owned workflow files under `.github/workflows/` (`ci.yml`, `claude.yml`, `claude-code-review.yml`), must contain:
  ```
  # Source: https://github.com/amulya-labs/ai-dev-foundry
  # License: MIT (https://opensource.org/licenses/MIT)
  ```
- CI enforces this (`validate-attributions` job)

## Testing Strategy

- **Fastest loop** (< 1 sec): `pytest tests/test_validate_bash.py -v`
- **Single test**: `pytest tests/test_validate_bash.py -v -k "docker_stop"`
- **Shell integration**: `tests/test-validate-bash.sh` (or `tests/test-validate-bash.sh allow`)
- **Ad-hoc pattern testing**: pipe JSON to the hook directly:
  ```bash
  echo '{"tool_input":{"command":"git status && ls"}}' | .claude/hooks/validate-bash.sh
  ```
- **manage-ai-configs**: `tests/test-manage-agents.sh`
- No external services or fixtures needed; all tests are self-contained

## Debug Playbook

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `Error: Python 3.11+ required` | Python < 3.11 without `tomli` | `pip install tomli` or upgrade Python |
| Hook returns empty output | Empty or malformed JSON input | Check structure: `{"tool_input":{"command":"..."}}` |
| Safe command triggers ASK | Command not in allow patterns, or ask pattern matches first | Check pattern order in `.ai-dev-foundry/shared/hooks/bash-policy/bash-patterns.toml` or OS-specific file; add allow pattern; add test case |
| OS-specific command triggers ASK | Pattern missing from OS overlay file | Add pattern to `.ai-dev-foundry/shared/hooks/bash-policy/bash-patterns.{linux,darwin,windows}.toml`; add test case with `os` field |
| Safe chain triggers ASK | One segment in the chain is unrecognized | The validator checks each segment independently; add an allow pattern for the unrecognized segment |
| CI fails: "missing attribution" | New/edited script missing header comments | Add `Source:` and `License:` comments (see Attribution above) |
| CI fails: agent frontmatter | Missing `name`/`description`/`source`/`license` or invalid YAML | Check frontmatter between `---` delimiters |
| Pattern regex error in stderr | Invalid regex in `bash-patterns.toml` | Python `re` module syntax applies; check the reported pattern |
| Hook logs filling disk | Logs at `/tmp/ai-dev-foundry-hook-logs/` with 15-day retention | Auto-cleanup runs daily; manual: `rm /tmp/ai-dev-foundry-hook-logs/*.log` |

## CI/CD Notes

- All CI jobs must pass for PRs to main — see `.github/workflows/ci.yml` for current job definitions and required checks
- CI runs on push to `main` and all PRs targeting `main`
- Reproduce locally: see full CI-equivalent command in Core Commands

## Guardrails

- **No secrets in this repo** -- it is a public config/agent collection
- **settings.local.json** is committed but intended for local-only permission overrides
- **Hook logs** go to `/tmp/ai-dev-foundry-hook-logs/` (not in repo); only ASK/DENY are logged; ALLOW is silent
- **Deny patterns** in `.ai-dev-foundry/shared/hooks/bash-policy/bash-patterns.toml` cannot be overridden -- this is by design for safety
- **Never push directly to main** -- always branch and PR
- **Do not add transient/status documentation to the repo** -- use GitHub issues for plans and tracking

## References

- `CONTRIBUTING.md` -- agent file format, hook architecture, script selection guide
- `README.md` -- user-facing install instructions, agent catalog, usage examples
- `.github/workflows/ci.yml` -- CI job definitions (source of truth for what must pass)
- `tests/bash-test-cases.toml` -- all hook test cases (data-driven)
