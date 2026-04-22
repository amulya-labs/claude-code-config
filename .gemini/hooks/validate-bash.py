#!/usr/bin/env python3
"""
Gemini CLI adapter for the provider-neutral Bash command validator.

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


def _read_tool_input(input_data: dict) -> dict:
    return input_data.get("tool_input", {}) or input_data.get("toolInput", {})


def output_decision(decision: str, reason: str) -> None:
    print(json.dumps({"decision": decision, "reason": reason}))


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
    request = {
        "provider": "gemini",
        "phase": "before_tool",
        "tool": "run_shell_command",
        "command": tool_input.get("command", "") or tool_input.get("commandLine", ""),
        "cwd": input_data.get("cwd", "") or tool_input.get("directory", ""),
    }

    if not request["command"]:
        sys.exit(0)

    decision, reason = validate_command_core.evaluate_request(request, str(config_path))
    output_decision(decision, reason)


if __name__ == "__main__":
    main()
