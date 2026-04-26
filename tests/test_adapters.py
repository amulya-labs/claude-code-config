"""
Contract tests for the per-provider hook adapters.

For each provider (Claude / Gemini / Codex), feeds a representative stdin
payload to the adapter script and asserts that:

  1. The adapter exits cleanly.
  2. Its stdout matches the JSON output schema the provider expects.
  3. The decision the adapter emits agrees with the shared policy engine
     for that command.

This catches adapter-level drift (key renames, schema changes, provider
output format regressions). It does NOT verify that the AI tools
themselves actually invoke the adapter under their hook config — that
requires end-to-end testing with the real CLIs and is tracked separately
(see #108 for the Codex-specific gap).

Source: https://github.com/amulya-labs/ai-dev-foundry
License: MIT (https://opensource.org/licenses/MIT)
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED_DIR = REPO_ROOT / ".ai-dev-foundry" / "shared" / "hooks" / "bash-policy"
CONFIG_PATH = SHARED_DIR / "bash-patterns.toml"

# Load the shared policy engine to compute expected decisions.
_spec = importlib.util.spec_from_file_location(
    "validate_bash_core", SHARED_DIR / "validate-command.py"
)
core = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = core
_spec.loader.exec_module(core)


ADAPTERS = {
    "claude": REPO_ROOT / ".claude" / "hooks" / "validate-bash.py",
    "gemini": REPO_ROOT / ".gemini" / "hooks" / "validate-bash.py",
    "codex": REPO_ROOT / ".codex" / "hooks" / "validate-bash.py",
}


def make_payload(provider: str, command: str) -> dict:
    """Return a representative stdin payload for the given provider.

    Shapes are derived from each tool's public hook documentation. The
    keys here must stay in sync with what each adapter reads.
    """
    if provider == "claude":
        return {
            "session_id": "test-session",
            "transcript_path": None,
            "cwd": str(REPO_ROOT),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    if provider == "gemini":
        return {
            "cwd": str(REPO_ROOT),
            "tool_input": {"command": command},
        }
    if provider == "codex":
        return {
            "session_id": "test-session",
            "cwd": str(REPO_ROOT),
            "hook_event_name": "PreToolUse",
            "turn_id": "test-turn",
            "tool_name": "Bash",
            "tool_use_id": "test-tool-use",
            "tool_input": {"command": command},
        }
    raise ValueError(f"unknown provider: {provider}")


def expected_decision(provider: str, command: str) -> str:
    """Decision the shared policy engine produces for this command.

    The ``tool`` and ``phase`` values here must mirror what each adapter
    passes to ``evaluate_request`` inside its ``validate-bash.py``.  If the
    request shape of any adapter changes (e.g. a key rename or a new phase
    string), update this helper to match or the skip-condition in
    ``test_adapter_decision_matches_policy`` will silently diverge.
    """
    request = {
        "provider": provider,
        "phase": "pre_tool_use" if provider != "gemini" else "before_tool",
        "tool": "run_shell_command" if provider == "gemini" else "bash",
        "command": command,
        "cwd": str(REPO_ROOT),
    }
    decision, _ = core.evaluate_request(request, str(CONFIG_PATH))
    return decision


def run_adapter(provider: str, payload: dict) -> tuple[int, str, str]:
    """Run the adapter as the CLI would: pipe JSON to stdin, capture stdout."""
    script = ADAPTERS[provider]
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


# Representative commands spanning all three policy verdicts.
SAMPLE_COMMANDS = [
    ("git status", "allow"),
    ("ls -la", "allow"),
    ("git push --force origin main", "ask"),
    ("rm -rf /", "deny"),
    ("sudo apt install foo", "deny"),
]


@pytest.mark.parametrize("command,expected_verdict", SAMPLE_COMMANDS)
@pytest.mark.parametrize("provider", list(ADAPTERS.keys()))
def test_adapter_decision_matches_policy(provider: str, command: str, expected_verdict: str) -> None:
    """Adapter's emitted decision agrees with the shared policy engine.

    Output protocol differs by provider:
      - Claude / Gemini: emit a JSON decision for every verdict (allow/ask/deny).
      - Codex: deny-only protocol — emit JSON only for deny; produce no stdout
        for allow or ask. Codex rejects any other permissionDecision value.
    """
    if expected_verdict != expected_decision(provider, command):
        pytest.skip(
            f"sample command '{command}' classified differently by policy on this OS; "
            "skip rather than block on platform-specific patterns"
        )

    payload = make_payload(provider, command)
    rc, stdout, stderr = run_adapter(provider, payload)
    assert rc == 0, f"{provider} adapter exited {rc}: stderr={stderr}"

    if provider == "codex" and expected_verdict != "deny":
        # Codex hook protocol: emit nothing for allow/ask. Codex's own approval
        # policy fills in for the missing prompt mechanism on `ask` patterns.
        assert not stdout.strip(), (
            f"codex adapter must produce no stdout for verdict={expected_verdict}; "
            f"got: {stdout!r}"
        )
        return

    if not stdout.strip():
        pytest.fail(f"{provider} adapter produced no stdout for command={command!r}")

    decision_payload = json.loads(stdout)

    if provider == "claude":
        block = decision_payload["hookSpecificOutput"]
        assert block["hookEventName"] == "PreToolUse"
        assert block["permissionDecision"] == expected_verdict
        assert "permissionDecisionReason" in block
    elif provider == "codex":
        # Only reached when expected_verdict == "deny".
        block = decision_payload["hookSpecificOutput"]
        assert block["hookEventName"] == "PreToolUse"
        assert block["permissionDecision"] == "deny"
        assert block["permissionDecisionReason"], "Codex requires non-empty reason on deny"
    elif provider == "gemini":
        assert decision_payload["decision"] == expected_verdict
        assert "reason" in decision_payload


@pytest.mark.parametrize("provider", list(ADAPTERS.keys()))
def test_adapter_handles_missing_command_gracefully(provider: str) -> None:
    """Empty payload should not crash any adapter."""
    payload = make_payload(provider, "")
    rc, stdout, stderr = run_adapter(provider, payload)
    assert rc == 0, f"{provider} adapter exited {rc} on empty command: stderr={stderr}"
    # No stdout expected when there's no command to evaluate.
    assert not stdout.strip(), f"{provider} adapter produced unexpected stdout: {stdout!r}"


@pytest.mark.parametrize("provider", list(ADAPTERS.keys()))
def test_adapter_handles_invalid_json(provider: str) -> None:
    """Malformed stdin must not crash the adapter (CLI may send garbage)."""
    script = ADAPTERS[provider]
    proc = subprocess.run(
        [sys.executable, str(script)],
        input="not-json",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"{provider} adapter crashed on bad JSON: {proc.stderr}"


def test_gemini_adapter_reads_camelcase_toolinput() -> None:
    """Gemini sends `toolInput` in some payload variants; adapter must handle both."""
    command = "git status"
    if expected_decision("gemini", command) != "allow":
        pytest.skip("policy verdict shifted; skip platform-specific")
    payload = {
        "cwd": str(REPO_ROOT),
        "toolInput": {"command": command},  # camelCase variant
    }
    rc, stdout, stderr = run_adapter("gemini", payload)
    assert rc == 0
    # Gemini's adapter must always emit a JSON decision (3-way protocol).
    # An empty stdout here means camelCase parsing silently failed — fail loudly.
    assert stdout.strip(), (
        "gemini adapter produced no stdout; camelCase toolInput parsing likely failed"
    )
    assert json.loads(stdout)["decision"] == "allow"
