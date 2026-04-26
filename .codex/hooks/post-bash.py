#!/usr/bin/env python3
"""
Codex PostToolUse adapter: logs ASK -> APPROVED transitions.

Codex's PreToolUse hook protocol is deny-only, so we can't piggy-back
on stdout to communicate the verdict to a downstream shell logger.
This script re-evaluates the command against the shared policy and,
if the original verdict was `ask`, writes the post-approval log line.

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


def main() -> None:
    config_path = Path(sys.argv[1]).resolve() if len(sys.argv) == 2 else CONFIG_PATH

    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = _read_tool_input(input_data)
    command = tool_input.get("command", "") or tool_input.get("commandLine", "")
    if not command:
        sys.exit(0)

    request = {
        "provider": "codex",
        "phase": "pre_tool_use",
        "tool": "bash",
        "command": command,
        "cwd": input_data.get("cwd", "") or tool_input.get("directory", ""),
    }
    decision, _ = validate_command_core.evaluate_request(request, str(config_path))

    # Only ask -> APPROVED is logged at post-hook time. Deny verdicts never
    # reach PostToolUse (Codex blocked them), and allow doesn't need an
    # audit trail beyond the initial pre-hook silent allow.
    if decision != "ask":
        sys.exit(0)

    directory = hook_log.log_dir()
    if not directory:
        sys.exit(0)

    project = hook_log.extract_project_from_input(input_data)
    hook_log.write_entry(
        directory,
        project,
        action="ASK -> APPROVED",
        command=command,
    )


if __name__ == "__main__":
    main()
