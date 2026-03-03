"""
Data-driven parametrized tests for the Bash command validator.

Loads test cases from bash-test-cases.toml and tests the validator
directly (no shell wrapper overhead).

Source: https://github.com/amulya-labs/ai-dev-foundry
License: MIT (https://opensource.org/licenses/MIT)
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Import validate-bash.py (hyphenated filename requires importlib)
HOOKS_DIR = Path(__file__).parent.parent / ".claude" / "hooks"
_spec = importlib.util.spec_from_file_location(
    "validate_bash", HOOKS_DIR / "validate-bash.py"
)
validate_bash = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_bash)

# Python 3.11+ has tomllib built-in
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

# ── Fixtures ──────────────────────────────────────────────────────────────────

TOML_PATH = Path(__file__).parent / "bash-test-cases.toml"
CONFIG_PATH = HOOKS_DIR / "bash-patterns.toml"


@pytest.fixture(scope="session")
def config():
    """Load the TOML pattern configuration once per session."""
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


@pytest.fixture(scope="session")
def compiled_patterns(config):
    """Compile all patterns once per session."""
    return {
        "deny": validate_bash.compile_patterns(config, "deny"),
        "ask": validate_bash.compile_patterns(config, "ask"),
        "allow": validate_bash.compile_patterns(config, "allow"),
    }


def load_test_cases():
    """Load test cases from TOML file."""
    with open(TOML_PATH, "rb") as f:
        data = tomllib.load(f)
    return data


# ── Parametrized test data ────────────────────────────────────────────────────

_test_data = load_test_cases()


def _make_ids(category):
    """Generate descriptive test IDs from test case descriptions."""
    return [case["description"] for case in _test_data.get(category, [])]


ALLOW_CASES = [
    (case["command"], case["description"])
    for case in _test_data.get("allow", [])
]

ASK_CASES = [
    (case["command"], case["description"])
    for case in _test_data.get("ask", [])
]

DENY_CASES = [
    (case["command"], case["description"])
    for case in _test_data.get("deny", [])
]


# ── Parametrized tests (data-driven from TOML) ───────────────────────────────


class TestAllowPatterns:
    """Commands that should be auto-approved."""

    @pytest.mark.parametrize(
        "command,description",
        ALLOW_CASES,
        ids=_make_ids("allow"),
    )
    def test_allow(self, compiled_patterns, command, description):
        decision, reason = validate_bash.validate_command(
            command,
            compiled_patterns["deny"],
            compiled_patterns["ask"],
            compiled_patterns["allow"],
        )
        assert decision == "allow", (
            f"Expected allow for '{description}'\n"
            f"  Command: {command}\n"
            f"  Got: {decision} ({reason})"
        )


class TestAskPatterns:
    """Commands that should prompt for confirmation."""

    @pytest.mark.parametrize(
        "command,description",
        ASK_CASES,
        ids=_make_ids("ask"),
    )
    def test_ask(self, compiled_patterns, command, description):
        decision, reason = validate_bash.validate_command(
            command,
            compiled_patterns["deny"],
            compiled_patterns["ask"],
            compiled_patterns["allow"],
        )
        assert decision == "ask", (
            f"Expected ask for '{description}'\n"
            f"  Command: {command}\n"
            f"  Got: {decision} ({reason})"
        )


class TestDenyPatterns:
    """Commands that should always be blocked."""

    @pytest.mark.parametrize(
        "command,description",
        DENY_CASES,
        ids=_make_ids("deny"),
    )
    def test_deny(self, compiled_patterns, command, description):
        decision, reason = validate_bash.validate_command(
            command,
            compiled_patterns["deny"],
            compiled_patterns["ask"],
            compiled_patterns["allow"],
        )
        assert decision == "deny", (
            f"Expected deny for '{description}'\n"
            f"  Command: {command}\n"
            f"  Got: {decision} ({reason})"
        )


# ── Unit tests for internal functions ─────────────────────────────────────────


class TestStripLineContinuations:
    """Test backslash-newline line continuation stripping."""

    @pytest.mark.parametrize(
        "input_cmd,expected",
        [
            ("echo test", "echo test"),
            ("echo test && \\\nkubectl get pods", "echo test &&  kubectl get pods"),
            ("\\\nkubectl get pods", " kubectl get pods"),
            ("no continuations here", "no continuations here"),
            ("multi\\\nline\\\ncmd", "multi line cmd"),
        ],
        ids=[
            "no-continuation",
            "after-ampersand",
            "leading-continuation",
            "plain-text",
            "multiple-continuations",
        ],
    )
    def test_strip(self, input_cmd, expected):
        assert validate_bash.strip_line_continuations(input_cmd) == expected


class TestSplitCommands:
    """Test command splitting on &&, ||, ;."""

    @pytest.mark.parametrize(
        "input_cmd,expected_count",
        [
            ("echo hello", 1),
            ("echo a && echo b", 2),
            ("echo a || echo b", 2),
            ("echo a; echo b", 2),
            ("echo a && echo b || echo c", 3),
            ("echo 'a && b'", 1),  # Quoted && not split
            ('echo "a; b"', 1),  # Quoted ; not split
            ("case $x in a);; b);; esac", 1),  # ;; not split
        ],
        ids=[
            "single",
            "and-chain",
            "or-chain",
            "semicolon",
            "triple-chain",
            "quoted-ampersand",
            "quoted-semicolon",
            "case-statement",
        ],
    )
    def test_split_count(self, input_cmd, expected_count):
        assert len(validate_bash.split_commands(input_cmd)) == expected_count


class TestStripEnvVars:
    """Test environment variable prefix stripping."""

    @pytest.mark.parametrize(
        "input_cmd,expected",
        [
            ("ls", "ls"),
            ("FOO=bar ls", "ls"),
            ("FOO=bar BAR=baz ls", "ls"),
            ('FOO="bar baz" ls', "ls"),
            ("FOO='bar baz' ls", "ls"),
            ("NODE_ENV=production npm test", "npm test"),
        ],
        ids=[
            "no-vars",
            "single-var",
            "multiple-vars",
            "double-quoted-var",
            "single-quoted-var",
            "real-world-node-env",
        ],
    )
    def test_strip(self, input_cmd, expected):
        assert validate_bash.strip_env_vars(input_cmd) == expected


class TestCleanSegment:
    """Test full segment cleaning pipeline."""

    @pytest.mark.parametrize(
        "input_seg,expected",
        [
            ("  echo hello  ", "echo hello"),
            ("(echo hello)", "echo hello"),
            ("{echo hello}", "echo hello"),
            ("FOO=bar echo hello", "echo hello"),
            ("\\\nkubectl get pods", "kubectl get pods"),
            ("then echo hello", "echo hello"),
            ("do ls -la", "ls -la"),
            ("done", ""),
            ("fi", ""),
            ("done < input.txt", ""),
        ],
        ids=[
            "whitespace",
            "parens",
            "braces",
            "env-var",
            "line-continuation",
            "then-keyword",
            "do-keyword",
            "done-terminator",
            "fi-terminator",
            "done-with-redirect",
        ],
    )
    def test_clean(self, input_seg, expected):
        assert validate_bash.clean_segment(input_seg) == expected


class TestExtractAssignments:
    """Test intra-chain variable assignment extraction."""

    @pytest.mark.parametrize(
        "input_seg,expected",
        [
            ("RUFF=/path/to/ruff", {"RUFF": "/path/to/ruff"}),
            ('FOO="bar baz"', {"FOO": "bar baz"}),
            ("FOO='bar baz'", {"FOO": "bar baz"}),
            ("FOO=$(cmd)", {}),
            ("FOO=$BAR", {}),
            ("FOO=bar BAZ=qux cmd", {"FOO": "bar", "BAZ": "qux"}),
            ("ls -la", {}),
            ("FOO=bar", {"FOO": "bar"}),
            ('A=1 B="two" C=3 cmd', {"A": "1", "B": "two", "C": "3"}),
            # ${VAR} braced form must be treated as dynamic (not captured as literal)
            ("FOO=${VENV_PATH}/bin/ruff", {}),
            ("FOO=${BAR}", {}),
            # $() inside double-quoted value must not be captured (security)
            ('FOO="$(rm -rf /)"', {}),
            ('FOO="`evil`"', {}),
            # ${VAR} inside double-quoted value must not be captured
            ('FOO="${BAR}/path"', {}),
            # Unclosed double-quote: do not capture
            ('FOO="unclosed', {}),
        ],
        ids=[
            "simple-unquoted",
            "double-quoted",
            "single-quoted",
            "command-substitution-skipped",
            "var-reference-skipped",
            "multiple-before-command",
            "no-assignments",
            "assignment-only",
            "mixed-quoting-styles",
            "braced-var-ref-skipped",
            "braced-var-only-skipped",
            "subshell-in-double-quotes-skipped",
            "backtick-in-double-quotes-skipped",
            "braced-var-in-double-quotes-skipped",
            "unclosed-double-quote-skipped",
        ],
    )
    def test_extract(self, input_seg, expected):
        assert validate_bash.extract_assignments(input_seg) == expected


class TestSubstituteKnownVars:
    """Test variable substitution at command position."""

    @pytest.mark.parametrize(
        "input_seg,env,expected",
        [
            ("$RUFF format src/", {"RUFF": "/path/ruff"}, "/path/ruff format src/"),
            ("${RUFF} format", {"RUFF": "/path/ruff"}, "/path/ruff format"),
            ("$UNKNOWN cmd", {}, "$UNKNOWN cmd"),
            ("git status", {"GIT": "/usr/bin/git"}, "git status"),
            ("$CMD", {"CMD": "ls"}, "ls"),
            ("${CMD}", {"CMD": "ls"}, "ls"),
        ],
        ids=[
            "dollar-var",
            "braced-var",
            "unknown-unchanged",
            "no-dollar-unchanged",
            "bare-var-no-args",
            "braced-var-no-args",
        ],
    )
    def test_substitute(self, input_seg, env, expected):
        assert validate_bash.substitute_known_vars(input_seg, env) == expected


class TestStripBashCWrapper:
    """Test bash -c / sh -c unwrapping."""

    @pytest.mark.parametrize(
        "input_seg,expected",
        [
            ('bash -c "git status"', "git status"),
            ("bash -c 'ls -la'", "ls -la"),
            ('/bin/bash -c "mypy src/"', "mypy src/"),
            ('sh -c "echo hello"', "echo hello"),
            ('/bin/sh -c "cat file.txt"', "cat file.txt"),
            ('bash -c "has \\"nested\\" quotes"', 'bash -c "has \\"nested\\" quotes"'),
            ("bash -n script.sh", "bash -n script.sh"),
            ("bash -c cmd_no_quotes", "bash -c cmd_no_quotes"),
            # Compound inner commands are NOT unwrapped (security: prevents deny bypass)
            ('bash -c "echo hi && rm -rf /"', 'bash -c "echo hi && rm -rf /"'),
            ("bash -c 'git status; git log'", "bash -c 'git status; git log'"),
            ('bash -c "ls | grep foo"', 'bash -c "ls | grep foo"'),
            ('bash -c "cmd $(date)"', 'bash -c "cmd $(date)"'),
            # Subshell grouping is NOT unwrapped (would bypass ^rm deny patterns)
            ('bash -c "(rm -rf /)"', 'bash -c "(rm -rf /)"'),
            # Redirect operators are NOT unwrapped (e.g. > /etc/cron.d/job)
            ('bash -c "> /etc/cron.d/job echo evil"', 'bash -c "> /etc/cron.d/job echo evil"'),
            ('bash -c "echo foo >> /etc/hosts"', 'bash -c "echo foo >> /etc/hosts"'),
            # Multi-line inner content is NOT unwrapped
            ('bash -c "line1\nline2"', 'bash -c "line1\nline2"'),
        ],
        ids=[
            "bash-double-quoted",
            "bash-single-quoted",
            "absolute-bash-path",
            "sh-double-quoted",
            "absolute-sh-path",
            "nested-quotes-unchanged",
            "not-c-flag-unchanged",
            "no-quotes-unchanged",
            "compound-and-unchanged",
            "compound-semicolon-unchanged",
            "compound-pipe-unchanged",
            "compound-subshell-unchanged",
            "subshell-grouping-unchanged",
            "redirect-overwrite-unchanged",
            "redirect-append-unchanged",
            "multiline-unchanged",
        ],
    )
    def test_strip(self, input_seg, expected):
        assert validate_bash.strip_bash_c_wrapper(input_seg) == expected


class TestStripControlFlowKeyword:
    """Test control flow keyword stripping."""

    @pytest.mark.parametrize(
        "input_seg,expected",
        [
            ("then echo hello", "echo hello"),
            ("else echo fallback", "echo fallback"),
            ("do ls -la", "ls -la"),
            ("elif [ -f x ]", "[ -f x ]"),
            ("done", ""),
            ("fi", ""),
            ("esac", ""),
            ("done < file.txt", ""),
            ("fi >> log.txt", ""),
            ("echo hello", "echo hello"),  # Not a keyword
            ("thermal sensor", "thermal sensor"),  # "then" substring
        ],
        ids=[
            "then",
            "else",
            "do",
            "elif",
            "done",
            "fi",
            "esac",
            "done-redirect",
            "fi-redirect",
            "not-keyword",
            "then-substring",
        ],
    )
    def test_strip(self, input_seg, expected):
        assert validate_bash.strip_control_flow_keyword(input_seg) == expected


# ── Edge case integration tests ───────────────────────────────────────────────


class TestEdgeCases:
    """Integration tests for tricky edge cases."""

    @pytest.mark.parametrize(
        "command,expected_decision",
        [
            ("export FOO=bar", "allow"),
            ("ps aux | grep nginx | awk '{print $2}'", "allow"),
            ("", "allow"),  # Empty command
            ("   ", "allow"),  # Whitespace only
        ],
        ids=[
            "export-statement",
            "complex-safe-pipeline",
            "empty-command",
            "whitespace-only",
        ],
    )
    def test_edge_case(self, compiled_patterns, command, expected_decision):
        decision, _ = validate_bash.validate_command(
            command,
            compiled_patterns["deny"],
            compiled_patterns["ask"],
            compiled_patterns["allow"],
        )
        assert decision == expected_decision


class TestGitRootPatternInjection:
    """Tests for dynamic git-root pattern injection.

    _inject_git_root_patterns() is called at runtime when Claude runs inside a
    git repo (cwd has .git).  These tests simulate that injection so the
    allow/ask behaviour can be exercised without actually touching the filesystem.
    """

    GIT_ROOT = "/home/user/myproject"

    @pytest.fixture(scope="class")
    def git_root_patterns(self):
        """Config with git root /home/user/myproject injected."""
        with open(CONFIG_PATH, "rb") as f:
            cfg = tomllib.load(f)
        validate_bash._inject_git_root_patterns(cfg, self.GIT_ROOT)
        return {
            "deny": validate_bash.compile_patterns(cfg, "deny"),
            "ask": validate_bash.compile_patterns(cfg, "ask"),
            "allow": validate_bash.compile_patterns(cfg, "allow"),
        }

    @pytest.mark.parametrize(
        "command,expected",
        [
            # Files within the git root are auto-approved
            ("rm /home/user/myproject/src/generated.py", "allow"),
            ("rm -rf /home/user/myproject/build/", "allow"),
            ("rm -f /home/user/myproject/tests/old_test.py", "allow"),
            ("rm -r /home/user/myproject/.venv/", "allow"),
            # .git directory itself is protected
            ("rm -rf /home/user/myproject/.git", "ask"),
            ("rm -rf /home/user/myproject/.git/", "ask"),
            ("rm -rf /home/user/myproject/.git/refs/", "ask"),
            # Paths outside the git root still ask
            ("rm /home/user/important.conf", "ask"),
            ("rm /home/otheruser/myproject/file.py", "ask"),
            ("rm /usr/local/lib/file.so", "ask"),
            ("rm -rf /var/log/myapp", "ask"),
            # Shallow home paths ask even with injection
            ("rm -rf /home/user", "ask"),
        ],
        ids=[
            "project-src-file-allowed",
            "project-build-dir-allowed",
            "project-test-file-allowed",
            "project-venv-allowed",
            "git-dir-bare-asks",
            "git-dir-slash-asks",
            "git-dir-refs-asks",
            "outside-repo-shallow-asks",
            "other-user-repo-asks",
            "system-path-asks",
            "var-log-asks",
            "shallow-home-asks",
        ],
    )
    def test_git_root_injection(self, git_root_patterns, command, expected):
        decision, reason = validate_bash.validate_command(
            command,
            git_root_patterns["deny"],
            git_root_patterns["ask"],
            git_root_patterns["allow"],
        )
        assert decision == expected, (
            f"Expected {expected} for '{command}'\n"
            f"  Got: {decision} ({reason})"
        )

    def test_add_to_negative_lookahead_basic(self):
        """_add_to_negative_lookahead inserts the exclusion at the first (?!."""
        pattern = r"^rm\s+/(?!tmp/|var/tmp/)"
        result = validate_bash._add_to_negative_lookahead(pattern, r"home/user/")
        assert result == r"^rm\s+/(?!home/user/|tmp/|var/tmp/)"

    def test_add_to_negative_lookahead_no_lookahead(self):
        """_add_to_negative_lookahead returns pattern unchanged if no (?!."""
        pattern = r"^rm\s+/tmp/"
        result = validate_bash._add_to_negative_lookahead(pattern, r"home/user/")
        assert result == pattern

    def test_add_to_negative_lookahead_only_first(self):
        """_add_to_negative_lookahead only modifies the first (?! occurrence."""
        pattern = r"^rm\s+/(?!tmp/).*(?!foo/)"
        result = validate_bash._add_to_negative_lookahead(pattern, r"home/user/")
        assert result == r"^rm\s+/(?!home/user/|tmp/).*(?!foo/)"


if __name__ == "__main__":
    # Allow running this file directly to execute the pytest suite.
    raise SystemExit(pytest.main([__file__]))
