# AI Dev Foundry

[![CI](https://github.com/amulya-labs/ai-dev-foundry/actions/workflows/ci.yml/badge.svg)](https://github.com/amulya-labs/ai-dev-foundry/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/amulya-labs/ai-dev-foundry/badge)](https://scorecard.dev/viewer/?uri=github.com/amulya-labs/ai-dev-foundry)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Agents](https://img.shields.io/badge/agents-25-blue.svg)](.claude/agents/)

**Production-ready configuration for AI coding agents.** A shared set of agents, a cross-tool Bash-command policy, and GitHub Actions workflows you can drop into any project. Works across Claude Code, Codex CLI, Gemini CLI, and opencode.

## Quick Install

```bash
mkdir -p scripts && curl -fsSL -o scripts/manage-ai-configs.sh https://raw.githubusercontent.com/amulya-labs/ai-dev-foundry/main/scripts/manage-ai-configs.sh && chmod +x scripts/manage-ai-configs.sh && ./scripts/manage-ai-configs.sh claude install
```

See [Installation Options](#installation-options) below for git-subtree, manual-copy, and update flows.

## Skip approvals for safe commands (opt-in)

If you've clicked **approve** on `git status`, `ls`, or `npm test` a dozen times in one session, you're paying for two approval layers stacked on top of each other: your AI tool's, and this repo's hook. You can collapse them into one, so the hook's `allow` list does the silent gating and prompts are reserved for commands that actually warrant a human check.

How it works today: the tool's native prompt fires *first*, before the hook gets a turn — so `allow` patterns never get to suppress it. The modes below skip the tool's prompt layer and let the hook be the sole gate for Bash.

| Tool | Start with |
|------|------------|
| **Claude Code** | `claude --dangerously-skip-permissions` |
| **Codex CLI** | `codex --full-auto` *(sandboxed)* |
| **Gemini CLI** | `gemini --yolo` (or `-y`) |

> **What stays in place.** The hook's `[deny.*]` patterns still fire in every mode above — destructive commands (`rm -rf /`, `dd of=/dev/*`, and similar) remain blocked. `[ask.*]` patterns still prompt for confirmation (e.g., force-pushes to `main`, destructive Docker/kubectl operations); `[allow.*]` patterns still auto-approve silently. You're not disabling the policy, you're promoting it to the sole gate.

### What this isn't

- **Not safer.** Same policy, enforced once instead of twice. Fewer prompts is a UX win, not a security upgrade.
- **Not a sandbox.** The hook blocks by pattern, not by capability. Use it on machines and repos you already trust — never on shared hosts or against code/prompts from untrusted sources.
- **Not set-and-forget.** Skim the allow/deny lists in [`.ai-dev-foundry/shared/hooks/bash-policy/bash-patterns.toml`](.ai-dev-foundry/shared/hooks/bash-policy/bash-patterns.toml) once before flipping, and again after upgrading this repo.

### When to stay on default prompts

- **First-time users** who haven't seen what the policy covers.
- **Windows / Git Bash** — the `bash-patterns.windows.toml` overlay is less battle-tested than Linux and macOS. Review your recent hook logs before switching on Windows.
- **opencode users** — there's no hook adapter for opencode in this repo yet (upstream hasn't exposed a comparable project hook surface), so YOLO on opencode means *no* gate, not "hook is the gate." Keep opencode on its default prompt settings until hook support lands.

See [CONTRIBUTING.md](CONTRIBUTING.md) for per-tool nuances (Codex sandbox semantics, Claude `settings.json` × hook precedence, logging) and for how to extend the allow/ask/deny patterns.

## Agents

See [docs/agents.md](docs/agents.md) for per-agent descriptions, domain grouping, and usage examples.

<table>
<tr><th>Agent</th><th>Description</th><th>Model</th></tr>
<tr><td>agent-specialist</td><td>Design and optimize AI agents with strong contracts</td><td rowspan="15">opus</td></tr>
<tr><td>career-advisor</td><td>Career strategy: role targeting, resume, job search, and pivots</td></tr>
<tr><td>claudemd-architect</td><td>Create and update CLAUDE.md files for agent-ready repos</td></tr>
<tr><td>data-engineer</td><td>ETL/ELT pipelines, data modeling, orchestration, and data quality</td></tr>
<tr><td>legal-counsel</td><td>Contract review, legal analysis, and policy drafting</td></tr>
<tr><td>marketing-lead</td><td>Positioning, messaging, and go-to-market copy</td></tr>
<tr><td>ml-architect</td><td>End-to-end ML system design and production ML decisions</td></tr>
<tr><td>prod-engineer</td><td>Production incident response and reliability engineering</td></tr>
<tr><td>product-owner</td><td>Product direction, prioritization, specs, and decisions</td></tr>
<tr><td>prompt-engineer</td><td>Engineer effective prompts for AI models</td></tr>
<tr><td>security-auditor</td><td>Security assessments and vulnerability identification</td></tr>
<tr><td>solution-eng</td><td>Technical sales, discovery, POCs, and solution design</td></tr>
<tr><td>systems-architect</td><td>High-level architecture guidance</td></tr>
<tr><td>tech-lead</td><td>Plan implementation approaches, break down tasks</td></tr>
<tr><td>ux-designer</td><td>UX critique covering usability, accessibility, and business alignment</td></tr>
<tr><td>code-reviewer</td><td>Thorough code reviews for quality and security</td><td rowspan="9">sonnet</td></tr>
<tr><td>pr-refiner</td><td>Refine PRs based on review feedback</td></tr>
<tr><td>debugger</td><td>Systematic bug investigation and root cause analysis</td></tr>
<tr><td>digital-designer</td><td>Print-ready layouts for booklets, brochures, posters</td></tr>
<tr><td>documentation-writer</td><td>Clear, minimal documentation following DRY principles</td></tr>
<tr><td>refactoring-expert</td><td>Improve code structure safely</td></tr>
<tr><td>senior-dev</td><td>Feature implementation with best practices</td></tr>
<tr><td>test-engineer</td><td>Comprehensive test suite design</td></tr>
<tr><td>ui-developer</td><td>Pixel-perfect UI implementation with design system thinking</td></tr>
<tr><td>junior-dev</td><td>Focused, well-scoped tasks for early-career developers</td><td>haiku</td></tr>
</table>

## Hooks

A shared Bash-command validation engine lives in `.ai-dev-foundry/shared/hooks/bash-policy/`, with thin adapters in `.claude/hooks/`, `.gemini/hooks/`, and `.codex/hooks/` — so the same allow/ask/deny policy is reused across Claude Code, Codex CLI, and Gemini CLI without duplicating rules. opencode is still pending upstream support for a comparable project hook surface.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the hook architecture, pattern categories, OS overlays, and the Claude `settings.json` × hook precedence matrix.

## GitHub Actions Workflows

| Workflow | Purpose | Trigger |
|----------|---------|---------|
| `claude-code-review.yml` | Automated PR review using Claude | PR opened/updated; `/review` or `/claude-review` comment |
| `gemini-code-review.yml` | Automated PR review using Gemini Flash + Pro | `/review` or `/gemini-review` comment |
| `sync-notebooklm.yml` | Flatten repo into text sources and sync to NotebookLM | Push to main; manual dispatch |

All `*-code-review.yml` workflows opt in to the shared `/review` PR comment trigger alongside any agent-specific commands such as `/claude-review` or `/gemini-review`. If both Claude and Gemini review workflows are installed, `/review` triggers both — disable the other workflow or use the provider-specific trigger to run only one.

**Gemini Code Review** needs a `GEMINI_API_KEY` repo secret. Flash drives the narrative PR summary; Pro drives line-level inline comments. Trigger manually with `/review` or `/gemini-review` on any PR (members/owners/collaborators only).

**NotebookLM Sync** — see [docs/notebooklm-sync.md](docs/notebooklm-sync.md) for install, source configuration, and secret setup.

## Scripts

| Script | Purpose |
|--------|---------|
| `manage-ai-configs.sh` | Install and update AI agent configs and GHA workflows via curl (no git knowledge needed) |
| `git-subtree-mgr` | Manage git subtrees with history tracking (install globally in `~/bin`) |

Run `./scripts/manage-ai-configs.sh` or `git-subtree-mgr --help` for usage.

| Use case | Recommended |
|----------|-------------|
| Just want the agents, minimal setup | `manage-ai-configs.sh claude install` |
| Also want Claude GitHub Actions workflows | `manage-ai-configs.sh claude install --with-gha-workflows` |
| Want git history of upstream changes | `git-subtree-mgr` |
| Managing multiple subtrees in a project | `git-subtree-mgr` |
| Non-technical team members | `manage-ai-configs.sh claude install` |

## Installation Options

<details>
<summary>Expand for curl / git-subtree / manual options</summary>

### Option 1: Curl-based Script (Recommended)

```bash
mkdir -p scripts
curl -fsSL -o scripts/manage-ai-configs.sh https://raw.githubusercontent.com/amulya-labs/ai-dev-foundry/main/scripts/manage-ai-configs.sh
chmod +x scripts/manage-ai-configs.sh
./scripts/manage-ai-configs.sh claude install
```

Update later with `./scripts/manage-ai-configs.sh claude update`.

### Option 2: Git Subtree

```bash
# Install the manager globally
curl -fsSL -o ~/bin/git-subtree-mgr https://raw.githubusercontent.com/amulya-labs/ai-dev-foundry/main/scripts/git-subtree-mgr
chmod +x ~/bin/git-subtree-mgr

# Add .claude as a subtree
git-subtree-mgr add --prefix=.claude --repo=amulya-labs/ai-dev-foundry --path=.claude
```

Update later with `git-subtree-mgr pull .claude`.

### Option 3: Manual Copy

```bash
git clone https://github.com/amulya-labs/ai-dev-foundry.git
cp -r ai-dev-foundry/.claude/ /path/to/your/project/.claude/
```

</details>

## Contributing

PRs welcome. Agents should be generalized (no project-specific references), focused (one domain per agent), and well-structured. See [CONTRIBUTING.md](CONTRIBUTING.md) for the agent file format, hook architecture, and guidelines.

## License

MIT
