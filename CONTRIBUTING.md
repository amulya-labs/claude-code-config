# Contributing

PRs welcome. When adding or improving agents, ensure they are generalized, focused, and well-structured.

## Agent File Format

```yaml
---
name: agent-name
description: Brief description of when to use this agent.
source: https://github.com/amulya-labs/ai-dev-foundry
license: MIT
model: opus  # optional: opus, sonnet, haiku (omit for default)
color: blue  # optional: terminal color
---

# Agent Title

Agent instructions in markdown...
```

**Required fields:** `name`, `description`, `source`, `license`

**Optional fields:** `model`, `color`

**Guidelines:**
- **Generalized** - No project-specific references
- **Focused** - One domain or task per agent
- **Well-structured** - Use clear sections with headers and bullets
- **Tested** - CI validates frontmatter syntax and attribution fields

## Hooks

The `.claude/hooks/` directory contains hooks that validate and log Bash commands.

| Hook | File | Purpose |
|------|------|---------|
| PreToolUse | `validate-bash.sh` | Validates commands before execution (allow/ask/deny) |
| PostToolUse | `post-bash.sh` | Logs outcomes of approved ASK commands |

### Bash Command Validation

The `validate-bash.sh` hook validates Bash commands against pattern lists in `bash-patterns.toml` and OS-specific overlay files.

**How it works:**

1. Claude Code calls PreToolUse before executing any Bash command
2. The hook checks the command against pattern lists (in order: deny, ask, allow)
3. Returns a decision: `allow` (auto-approve), `ask` (prompt user), or `deny` (block)
4. If user approves an ASK command, PostToolUse logs the approval

**Pattern categories:**

| Category | Behavior | Examples |
|----------|----------|----------|
| `[deny.*]` | Always block, no override | `sudo`, `rm -rf /`, `dd of=/dev/` |
| `[ask.*]` | Prompt user for confirmation | `git push --force`, `docker stop`, `kubectl delete` |
| `[allow.*]` | Auto-approve silently | `git status`, `ls`, `npm test`, `kubectl get` |

### Customizing Patterns

Edit `.claude/hooks/bash-patterns.toml` for cross-platform patterns, or the OS-specific overlay files for platform-specific commands:

| File | Platform | When loaded |
|------|----------|-------------|
| `bash-patterns.toml` | All | Always (base) |
| `bash-patterns.linux.toml` | Linux | `sys.platform == 'linux'` |
| `bash-patterns.darwin.toml` | macOS | `sys.platform == 'darwin'` |
| `bash-patterns.windows.toml` | Windows (Git Bash) | `sys.platform in ('win32', 'cygwin', 'msys')` |

OS overlay patterns are **appended** to matching base sections. New sections are added as-is. Base deny patterns are never weakened.

```toml
# In bash-patterns.toml (universal):
[allow.my_tools]
description = "My custom tools"
patterns = [
    "^mytool ",
    "^another-tool ",
]

# In bash-patterns.darwin.toml (macOS-only):
[allow.my_tools]
description = "macOS-specific tools (extends base)"
patterns = [
    "^mac-only-tool ",
]
```

Patterns are regular expressions. Use `^` to anchor to the start of the command.

### Logging

Hooks log `ASK` and `DENY` decisions (not `ALLOW`) to reduce disk I/O:

- **Location:** `/tmp/claude-hook-logs/`
- **Format:** `YYYY-MM-DD-Day-<project>.log`
- **Retention:** 15 days (auto-cleanup)

<details>
<summary>Log format example</summary>

```
========================================
TIME:   2026-02-15 04:43:42
ACTION: ASK
REASON: 'git rebase main' matches ask.git_destructive
CMD:    git rebase main
========================================
========================================
TIME:   2026-02-15 04:43:45
ACTION: ASK -> APPROVED
CMD:    git rebase main
========================================
```

</details>

## Which Script Should I Use?

| Use case | Recommended |
|----------|-------------|
| Just want the agents, minimal setup | `manage-ai-configs.sh claude install` |
| Also want Claude GitHub Actions workflows | `manage-ai-configs.sh claude install --with-gha-workflows` |
| Want git history of upstream changes | `git-subtree-mgr` |
| Managing multiple subtrees in a project | `git-subtree-mgr` |
| Non-technical team members | `manage-ai-configs.sh claude install` |
