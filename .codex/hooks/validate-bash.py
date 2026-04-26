#!/usr/bin/env python3
"""
Codex adapter for the provider-neutral Bash command validator.

Source: https://github.com/amulya-labs/ai-dev-foundry
License: MIT (https://opensource.org/licenses/MIT)
"""

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SHARED_DIR = SCRIPT_DIR.parent.parent / ".ai-dev-foundry" / "shared" / "hooks" / "bash-policy"
CONFIG_PATH = SHARED_DIR / "bash-patterns.toml"
CORE_PATH = SHARED_DIR / "validate-command.py"

_spec = importlib.util.spec_from_file_location("aidf_validate_command", CORE_PATH)
validate_command_core = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = validate_command_core
_spec.loader.exec_module(validate_command_core)

sys.path.insert(0, str(SHARED_DIR))
import hook_log  # noqa: E402


def _read_tool_input(input_data: dict) -> dict:
    return input_data.get("tool_input", {}) or input_data.get("toolInput", {})


def output_decision(decision: str, reason: str) -> None:
    """Emit a Codex-compatible PreToolUse hook response.

    Codex's PreToolUse hook protocol is deny-only: only `permissionDecision: "deny"`
    (with a non-empty reason) is recognized. Any other value — including "allow"
    or "ask" — is rejected by Codex with "unsupported permissionDecision:...".

    Mapping for our 3-way (allow/ask/deny) policy under Codex:
      - deny  -> emit the deny JSON; Codex blocks the command.
      - allow -> emit nothing; Codex proceeds normally.
      - ask   -> emit nothing; defer to Codex's own approval policy, which
                 already prompts for commands outside its trusted set.
    """
    if decision != "deny":
        return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason or "Blocked by ai-dev-foundry deny pattern",
                }
            }
        )
    )


def log_decision(input_data: dict, command: str, decision: str, reason: str) -> None:
    """Audit-log ask/deny verdicts.

    Codex's deny-only stdout contract means we can't piggy-back on the
    adapter's stdout to drive shell-side logging the way the Claude/Gemini
    wrappers do. Instead the adapter writes the log entry itself — same
    format, same log file, no shell parsing required.
    """
    if decision not in ("ask", "deny"):
        return
    directory = hook_log.log_dir()
    if not directory:
        return
    project = hook_log.extract_project_from_input(input_data)
    hook_log.write_entry(
        directory,
        project,
        action=decision.upper(),
        command=command,
        reason=reason,
    )


def main() -> None:
    if len(sys.argv) > 2:
        print("Usage: validate-bash.py [config.toml]", file=sys.stderr)
        sys.exit(1)

    config_path = Path(sys.argv[1]).resolve() if len(sys.argv) == 2 else CONFIG_PATH

    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = _read_tool_input(input_data)
    command = tool_input.get("command", "") or tool_input.get("commandLine", "")
    request = {
        "provider": "codex",
        "phase": "pre_tool_use",
        "tool": "bash",
        "command": command,
        "cwd": input_data.get("cwd", "") or tool_input.get("directory", ""),
    }

    if not command:
        sys.exit(0)

    decision, reason = validate_command_core.evaluate_request(request, str(config_path))
    log_decision(input_data, command, decision, reason)
    output_decision(decision, reason)


if __name__ == "__main__":
    main()
