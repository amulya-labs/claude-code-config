#!/usr/bin/env python3
"""
Bash command validator for Claude Code PreToolUse hook.
Reads patterns from TOML config and validates commands.

Source: https://github.com/amulya-labs/ai-dev-foundry
License: MIT (https://opensource.org/licenses/MIT)
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Python 3.11+ has tomllib built-in
try:
    import tomllib
except ImportError:
    # Fallback for Python < 3.11
    try:
        import tomli as tomllib
    except ImportError:
        print(
            "Error: Python 3.11+ required, or install 'tomli' package for older versions",
            file=sys.stderr,
        )
        sys.exit(1)


@dataclass
class CompiledPattern:
    """A pre-compiled regex pattern with metadata."""
    regex: re.Pattern
    section: str
    original: str


def load_config(config_path: str) -> dict:
    """Load and validate TOML configuration."""
    try:
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        print(f"Error: Invalid TOML in {config_path}: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)


def detect_os() -> str:
    """Detect OS platform and return the suffix for OS-specific config files.

    Returns 'linux', 'darwin', or 'windows'. Falls back to sys.platform value.
    """
    platform = sys.platform
    if platform.startswith("linux"):
        return "linux"
    elif platform == "darwin":
        return "darwin"
    elif platform in ("win32", "cygwin", "msys"):
        return "windows"
    return platform


def merge_os_config(base: dict, overlay: dict) -> dict:
    """Merge OS-specific config into base config with additive-append semantics.

    For each category (deny/ask/allow), OS patterns are appended to matching
    base sections. New sections from the overlay are added as-is.
    Base patterns are never removed or replaced.
    """
    for category in ("deny", "ask", "allow"):
        overlay_sections = overlay.get(category, {})
        if not overlay_sections:
            continue
        base.setdefault(category, {})
        for section_name, section_data in overlay_sections.items():
            if not isinstance(section_data, dict) or "patterns" not in section_data:
                continue
            if section_name in base[category] and isinstance(base[category][section_name], dict):
                # Append OS patterns after base patterns
                base[category][section_name]["patterns"] = (
                    base[category][section_name].get("patterns", [])
                    + section_data["patterns"]
                )
            else:
                # New section from OS overlay
                base[category][section_name] = section_data
    return base


def compile_patterns(config: dict, category: str) -> list[CompiledPattern]:
    """Extract and compile patterns for a category (deny/ask/allow).

    Pre-compiling regex patterns improves performance significantly
    when validating many commands.
    """
    compiled = []
    for section_name, section in config.get(category, {}).items():
        if isinstance(section, dict) and "patterns" in section:
            for pattern in section["patterns"]:
                try:
                    regex = re.compile(pattern)
                    compiled.append(CompiledPattern(
                        regex=regex,
                        section=f"{category}.{section_name}",
                        original=pattern
                    ))
                except re.error as e:
                    print(f"Warning: Invalid regex '{pattern}' in {category}.{section_name}: {e}",
                          file=sys.stderr)
    return compiled


def strip_env_vars(cmd: str) -> str:
    """Strip environment variable assignments from command start.

    Handles: VAR=value, VAR="value", VAR='value', VAR=$(cmd), VAR=$VAR
    """
    while True:
        cmd = cmd.lstrip()
        match = re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', cmd)
        if not match:
            break

        rest = cmd[match.end():]

        if rest.startswith('$('):
            # Command substitution $(...)
            depth = 1
            i = 2
            while depth > 0 and i < len(rest):
                if rest[i] == '(':
                    depth += 1
                elif rest[i] == ')':
                    depth -= 1
                i += 1
            cmd = rest[i:]
        elif rest.startswith('`'):
            # Backtick substitution
            end = rest.find('`', 1)
            cmd = rest[end + 1:] if end > 0 else ""
        elif rest.startswith('"'):
            # Double-quoted value
            i = 1
            while i < len(rest):
                if rest[i] == '\\' and i + 1 < len(rest):
                    i += 2
                    continue
                if rest[i] == '"':
                    break
                i += 1
            cmd = rest[i + 1:]
        elif rest.startswith("'"):
            # Single-quoted value
            end = rest.find("'", 1)
            cmd = rest[end + 1:] if end > 0 else ""
        elif rest.startswith('$') and len(rest) > 1 and re.match(r'[A-Za-z_]', rest[1]):
            # Variable reference $VAR
            var_match = re.match(r'^\$[A-Za-z_][A-Za-z0-9_]*', rest)
            cmd = rest[var_match.end():] if var_match else rest
        else:
            # Unquoted value - ends at whitespace
            val_match = re.match(r'^[^\s]*\s*', rest)
            cmd = rest[val_match.end():] if val_match else ""

    return cmd.lstrip()


def strip_leading_comment(cmd: str) -> str:
    """Strip shell comments from the start of a command.

    Handles multi-line commands where the first line is a comment.
    """
    lines = cmd.split('\n')
    while lines and lines[0].strip().startswith('#'):
        lines.pop(0)
    return '\n'.join(lines).lstrip()


def _find_matching_paren(cmd: str, start: int) -> int:
    """Find the matching ')' for a '(' that was just consumed.

    Tracks nested parentheses, quotes (single and double), and heredoc bodies
    so that a ')' inside a heredoc is not mistaken for the closing paren.
    Returns the index one past the matching ')'.
    Returns len(cmd) if unmatched (unclosed substitution).
    """
    depth = 1
    i = start
    inner_quote = None
    heredoc_delim = None  # active heredoc delimiter (str) inside $()

    while i < len(cmd) and depth > 0:
        char = cmd[i]

        # --- Heredoc mode inside $(): consume body until delimiter ---
        if heredoc_delim is not None:
            if char == '\n':
                next_nl = cmd.find('\n', i + 1)
                if next_nl == -1:
                    next_nl = len(cmd)
                raw_line = cmd[i + 1:next_nl]
                # Use exact match only (<<- stripping is unusual inside $(); treat
                # conservatively and do not strip tabs so we don't close too early).
                if raw_line == heredoc_delim:
                    i = next_nl  # skip to end of delimiter line
                    heredoc_delim = None
                    continue
            i += 1
            continue

        # Skip escaped characters — but NOT inside single quotes, where backslash
        # has no special meaning in bash (\' does NOT escape the closing single quote).
        if char == '\\' and i + 1 < len(cmd) and inner_quote != "'":
            i += 2
            continue

        if char in ('"', "'"):
            if inner_quote is None:
                inner_quote = char
            elif inner_quote == char:
                inner_quote = None
        elif inner_quote is None:
            # Detect heredoc operator << (but not <<<) to enter heredoc mode
            if char == '<' and cmd[i:i+2] == '<<' and (i + 2 >= len(cmd) or cmd[i + 2] != '<'):
                delim, _strip, end_pos = _parse_heredoc_delim(cmd, i + 2)
                if delim:
                    i = end_pos
                    heredoc_delim = delim
                    continue
            elif char == '(':
                depth += 1
            elif char == ')':
                depth -= 1

        i += 1

    return i


def _parse_heredoc_delim(cmd: str, pos: int) -> tuple:
    """Parse a heredoc delimiter starting after '<<'.

    Handles: <<DELIM, <<'DELIM', <<"DELIM", <<-DELIM, <<-'DELIM'
    Returns (delimiter_str, strip_tabs, end_pos) or (None, False, pos) if invalid.
    strip_tabs is True for <<- heredocs (bash strips leading tabs from body lines
    and the delimiter line); False for regular << (exact delimiter match required).
    """
    j = pos
    # Check for optional '-' (<<- strips leading tabs from body and delimiter)
    strip_tabs = False
    if j < len(cmd) and cmd[j] == '-':
        strip_tabs = True
        j += 1
    # Skip optional whitespace between << / <<- and the delimiter
    while j < len(cmd) and cmd[j] in ' \t':
        j += 1
    if j >= len(cmd) or cmd[j] == '\n':
        return None, False, pos

    if cmd[j] in ("'", '"'):
        # Quoted delimiter: <<'EOF' or <<"EOF"
        delim_quote = cmd[j]
        k = cmd.find(delim_quote, j + 1)
        if k > j + 1:
            return cmd[j + 1:k], strip_tabs, k + 1
        return None, False, pos
    else:
        # Unquoted delimiter: word characters only
        m = re.match(r'[A-Za-z_][A-Za-z0-9_]*', cmd[j:])
        if m:
            return m.group(0), strip_tabs, j + m.end()
        return None, False, pos


def split_commands(cmd: str) -> list[str]:
    """Split command on &&, ||, ;, newlines (respecting quotes, comments, and shell syntax).

    Special handling for:
    - ;; (case statement terminator) - not a split point
    - Quoted strings
    - Bare newlines are command separators (like ;) in shell
    - $(...) command substitutions - consumed as atomic chunks
    - Here-documents (<< DELIM) - content consumed until delimiter line
    """
    segments = []
    current = ""
    quote = None
    i = 0
    heredoc_delim = None       # Active heredoc delimiter string, or None
    heredoc_strip_tabs = False  # True for <<- (strip leading tabs from delimiter line)

    while i < len(cmd):
        char = cmd[i]

        # --- Heredoc mode: consume everything until the closing delimiter ---
        if heredoc_delim is not None:
            current += char
            if char == '\n':
                # Check if the next line is the heredoc delimiter.
                # For <<- heredocs, bash strips leading TABS (not spaces) from the
                # delimiter line before comparing.  For regular << heredocs the line
                # must match the delimiter exactly (no leading whitespace allowed).
                next_nl = cmd.find('\n', i + 1)
                if next_nl == -1:
                    next_nl = len(cmd)
                raw_line = cmd[i + 1:next_nl]
                candidate = raw_line.lstrip('\t') if heredoc_strip_tabs else raw_line
                if candidate == heredoc_delim:
                    # Consume the delimiter line and exit heredoc mode
                    current += raw_line
                    i = next_nl
                    heredoc_delim = None
                    heredoc_strip_tabs = False
                    continue
            i += 1
            continue

        # Track quotes (ignore escaped by odd number of backslashes)
        if char in ('"', "'"):
            backslash_count = 0
            j = i - 1
            while j >= 0 and cmd[j] == '\\':
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:
                if quote is None:
                    quote = char
                elif quote == char:
                    quote = None

        # --- $(...) command substitution: consume as atomic chunk ---
        # Valid in unquoted context and inside double quotes (not single quotes).
        if char == '$' and i + 1 < len(cmd) and cmd[i + 1] == '(' and quote != "'":
            end = _find_matching_paren(cmd, i + 2)
            current += cmd[i:end]
            i = end
            continue

        # Split on && || ; \n outside quotes
        if quote is None:
            # --- Heredoc operator << (but not <<< here-string) ---
            if char == '<' and cmd[i:i+2] == '<<' and (i + 2 >= len(cmd) or cmd[i + 2] != '<'):
                delim, strip_tabs, end_pos = _parse_heredoc_delim(cmd, i + 2)
                if delim:
                    current += cmd[i:end_pos]
                    i = end_pos
                    heredoc_delim = delim
                    heredoc_strip_tabs = strip_tabs
                    continue
                # Not a valid heredoc, fall through to normal processing

            if cmd[i:i+2] in ('&&', '||'):
                if current.strip():
                    segments.append(current)
                current = ""
                i += 2
                continue
            elif char == ';':
                # Don't split on ;; (case statement terminator)
                if cmd[i:i+2] == ';;':
                    current += ';;'
                    i += 2
                    continue
                # Don't split on \; (escaped semicolon, e.g. find -exec cmd {} \;)
                if current.endswith('\\'):
                    current += char
                    i += 1
                    continue
                if current.strip():
                    segments.append(current)
                current = ""
                i += 1
                continue
            elif char == '\n':
                # \<newline> is a line continuation in shell — do NOT split
                if current.endswith('\\'):
                    current = current[:-1]  # strip the trailing backslash
                    i += 1
                    continue
                if current.strip():
                    segments.append(current)
                current = ""
                i += 1
                continue

        current += char
        i += 1

    if current.strip():
        segments.append(current)

    return segments


# Shell control flow keywords that may prefix body commands
# These keywords introduce blocks but the body commands need separate validation
CONTROL_FLOW_KEYWORDS = re.compile(
    r'^(then|else|elif|do)\s+',
    re.IGNORECASE
)

# Shell control flow terminators that may have redirections attached
# These complete control structures and are safe on their own
# Matches: done, fi, esac — optionally followed by redirections like
# 2>/dev/null, | sort, 2>&1 | head, etc.
CONTROL_FLOW_TERMINATORS = re.compile(
    r'^(done|fi|esac)(\s+[\d<>|&].*|\s*$)',
    re.IGNORECASE
)


def extract_assignments(segment: str) -> dict[str, str]:
    """Extract VAR=literal assignments from a raw segment.

    Only captures literal values (unquoted, single-quoted, double-quoted).
    Dynamic values ($(...), backticks, $VAR references) are skipped.

    Returns dict of {name: value} for captured assignments.
    """
    env = {}
    rest = segment.lstrip()
    while True:
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', rest)
        if not match:
            break

        name = match.group(1)
        rest = rest[match.end():]

        if rest.startswith('$(') or rest.startswith('`'):
            # Dynamic command substitution — skip entire assignment, stop capturing
            break
        elif rest.startswith('${'):
            # Braced variable reference ${VAR} or ${VAR}/path — skip
            break
        elif rest.startswith('$') and len(rest) > 1 and re.match(r'[A-Za-z_]', rest[1]):
            # Variable reference $VAR — skip
            break
        elif rest.startswith('"'):
            # Double-quoted value — parse respecting escape sequences
            i = 1
            while i < len(rest):
                if rest[i] == '\\' and i + 1 < len(rest):
                    i += 2
                    continue
                if rest[i] == '"':
                    break
                i += 1
            else:
                # Unclosed double-quote: do not capture a potentially truncated value
                break
            captured = rest[1:i]
            # Reject if value contains command substitution or variable expansion
            # e.g. FOO="$(evil)" or FOO="${BAR}/path" — treat as dynamic
            if '$(' in captured or '`' in captured or '${' in captured:
                break
            env[name] = captured
            rest = rest[i + 1:]
        elif rest.startswith("'"):
            # Single-quoted value
            end = rest.find("'", 1)
            if end > 0:
                env[name] = rest[1:end]
                rest = rest[end + 1:]
            else:
                break
        else:
            # Unquoted value — ends at whitespace
            val_match = re.match(r'^([^\s]*)', rest)
            env[name] = val_match.group(1) if val_match else ""
            rest = rest[val_match.end():] if val_match else ""

        rest = rest.lstrip()

    return env


def substitute_known_vars(segment: str, env: dict[str, str]) -> str:
    """Replace $VAR or ${VAR} at position 0 of a cleaned segment with known values.

    Only substitutes if the variable name exists in env.
    Only operates at position 0 (the command itself), not arguments.
    """
    if not segment.startswith('$'):
        return segment

    # Try ${VAR} form first
    match = re.match(r'^\$\{([A-Za-z_][A-Za-z0-9_]*)\}', segment)
    if match:
        name = match.group(1)
        if name in env:
            return env[name] + segment[match.end():]
        return segment

    # Try $VAR form
    match = re.match(r'^\$([A-Za-z_][A-Za-z0-9_]*)', segment)
    if match:
        name = match.group(1)
        if name in env:
            return env[name] + segment[match.end():]

    return segment


# Shell metacharacters that indicate a compound or redirecting inner command.
# If any of these appear inside a bash -c argument, the inner command is left
# unwrapped so split_commands() can process it correctly and deny patterns
# are not bypassed.
# Includes:
#   &&, ||      — logical operators
#   ;           — sequential separator
#   |           — pipe
#   $(, `       — command substitution
#   (           — subshell grouping (e.g. (rm -rf /) would bypass ^rm deny)
#   >>?, >&     — redirect operators (e.g. > /etc/cron.d/job bypasses patterns)
_SHELL_META = re.compile(r'&&|\|\||;|\||\$\(|`|\(|>>?|>&')


def strip_bash_c_wrapper(segment: str) -> str:
    """Unwrap simple bash -c / sh -c wrappers to expose the inner command.

    Handles: bash -c "cmd", bash -c 'cmd', sh -c "cmd", /bin/bash -c "cmd", etc.
    Complex quoting (nested quotes, escaped quotes inside) is left unchanged.
    Compound inner commands (containing &&, ||, ;, |, $(), backtick, subshell
    grouping, or redirects) are left unchanged so split_commands() can process
    them correctly and deny patterns are not bypassed.

    Known limitation: flags before -c (e.g. 'bash -e -c "cmd"') are NOT
    unwrapped because the regex requires -c immediately after the binary name.
    These fall back to the ask-by-default path, which is safe and conservative.
    """
    match = re.match(r'^(?:/bin/)?(bash|sh)\s+-c\s+([\'"])(.*)\2\s*$', segment, re.DOTALL)
    if not match:
        return segment

    delimiter = match.group(2)
    inner = match.group(3)

    # Reject multi-line input: embedded newlines indicate complex quoting
    if '\n' in inner or '\r' in inner:
        return segment

    # Reject if inner contains the delimiter at all (escaped or not)
    # Any occurrence means the quoting is complex enough to skip unwrapping
    if delimiter in inner:
        return segment

    # Reject compound inner commands: split_commands() handles these correctly
    # on the un-wrapped form; unwrapping here would skip that splitting and
    # let e.g. 'bash -c "echo hi && rm -rf /"' bypass deny patterns.
    if _SHELL_META.search(inner):
        return segment

    return inner


def strip_control_flow_keyword(segment: str) -> str:
    """Strip shell control flow keywords from segment start.

    When commands like 'if condition; then body; fi' are split on ';',
    we get segments like 'then body'. This function strips the 'then '
    prefix so 'body' can be validated independently.

    For terminators like 'done < file.txt', we return empty string since
    the redirection is part of the loop construct, not a separate command.

    Returns the segment with any leading control flow keyword removed,
    or empty string for terminators (which are inherently safe).
    """
    # Check for terminators first (done, fi, esac) - possibly with redirection
    if CONTROL_FLOW_TERMINATORS.match(segment):
        return ""

    # Check for body-introducing keywords (then, else, do, etc.)
    match = CONTROL_FLOW_KEYWORDS.match(segment)
    if match:
        return segment[match.end():].lstrip()

    return segment


def strip_line_continuations(cmd: str) -> str:
    r"""Strip shell line continuations (\<newline>) from command.

    In shell, a backslash followed by a newline is a line continuation
    that joins lines. Claude Code sends multi-line commands with these
    preserved in the JSON, e.g.:
        echo "test" && \
        kubectl get pods
    becomes: 'echo "test" && \\\nkubectl get pods'

    After splitting on &&, the second segment starts with '\\\n' which
    must be removed before pattern matching.
    """
    return cmd.replace('\\\n', ' ')


def _is_bare_redirect(segment: str) -> bool:
    """Check if a segment is just a bare redirect/pipe tail with no real command.

    After chain splitting, residual segments like '2>/dev/null', '| head -5',
    '2>/dev/null | head -15', or '2>&1' can appear. These are not commands
    and should be skipped.
    """
    # Strip all redirections: N>/dev/null, 2>&1, >/dev/null, N>>/path, etc.
    s = re.sub(r'\d*>>?&?\d*/?[\w./-]*', '', segment)
    # Strip pipe + safe tail commands (head, tail, sort, wc, tee, grep, etc.)
    s = re.sub(r'\|\s*(head|tail|sort|wc|uniq|tee|grep|sed|awk|cut|tr|column|less|more|cat|fmt|nl|rev|paste|comm|fold|expand|unexpand|pr)\b[^|]*', '', s)
    # Strip remaining pipes to nothing
    s = re.sub(r'\|', '', s)
    return not s.strip()


def _strip_case_label(segment: str) -> str:
    """Strip case-statement pattern labels from segment start.

    When 'case $var in pattern) cmd ;; esac' is split on ;;/;,
    we get segments like 'pattern) cmd'. Strip the 'pattern) ' prefix
    so 'cmd' can be validated independently.

    Case labels are typically short identifiers/globs without spaces:
      foo) cmd        yes|no) cmd        *.txt) cmd        *) cmd
    We require NO spaces before ) to avoid false matches on commands
    containing (...) like 'kubectl exec ... -c "count(*) FROM ..."'.
    """
    # Match: non-space, non-$, non-newline chars followed by ) then space
    # The \S+ ensures no spaces before ), distinguishing case labels from
    # commands that happen to contain (...)
    match = re.match(r'^(\S+)\)\s+', segment)
    if match and '$(' not in match.group(1) and '(' not in match.group(1):
        return segment[match.end():]
    return segment


def clean_segment(segment: str) -> str:
    """Clean a command segment: strip whitespace, subshell chars, env vars, comments."""
    segment = segment.strip()

    # Strip residual line continuation backslash (safety net for edge cases
    # where strip_line_continuations() in validate_command() didn't catch it)
    while segment.startswith('\\') and (len(segment) == 1 or segment[1] in ' \t\n'):
        segment = segment[1:].lstrip()

    # Strip leading comments
    segment = strip_leading_comment(segment)

    # Strip env vars BEFORE subshell stripping so that VAR=$(...) is consumed
    # intact. If we strip trailing ) first, it breaks the $() matching.
    segment = strip_env_vars(segment)

    # After stripping env vars, if nothing remains, it was a pure assignment
    if not segment.strip():
        return ""

    # Strip leading subshell/grouping: ( {
    while segment and segment[0] in '({':
        segment = segment[1:].lstrip()

    # Strip trailing subshell/grouping: ) }
    while segment and segment[-1] in ')}':
        segment = segment[:-1].rstrip()

    # Check for bare redirects / pipe tails (e.g. "2>/dev/null | head -5")
    if _is_bare_redirect(segment):
        return ""

    # Unwrap bash -c / sh -c wrappers to expose the inner command
    segment = strip_bash_c_wrapper(segment)

    # Strip shell control flow keywords (then, else, do, etc.)
    # This allows validation of the body command within control structures
    segment = strip_control_flow_keyword(segment)

    # Strip case-statement pattern labels (e.g. "pattern) grep -q ...")
    segment = _strip_case_label(segment)

    # Re-run strip_env_vars after control flow stripping. A segment like
    # "do name=$(cmd)" becomes "name=$(cmd)" after keyword stripping,
    # which is now a pure assignment that should be consumed.
    segment = strip_env_vars(segment)
    if not segment.strip():
        return ""

    return segment


def check_patterns(segment: str, patterns: list[CompiledPattern]) -> tuple[bool, str]:
    """Check if segment matches any compiled pattern.

    Returns (matched, section_name).
    """
    for pattern in patterns:
        if pattern.regex.search(segment):
            return True, pattern.section
    return False, ""


def output_decision(decision: str, reason: str):
    """Output JSON decision for Claude Code hook."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason
        }
    }))


def validate_command(
    command: str,
    deny_patterns: list[CompiledPattern],
    ask_patterns: list[CompiledPattern],
    allow_patterns: list[CompiledPattern]
) -> tuple[str, str]:
    """Validate a command against patterns.

    Returns (decision, reason) tuple.
    Decision is one of: "deny", "ask", "allow"
    """
    # Strip line continuations BEFORE any processing. In shell, \<newline>
    # is purely a visual line continuation with no semantic meaning.
    # Must happen before split_commands() so segments don't start with '\'
    command = strip_line_continuations(command)

    # First, check DENY patterns against the FULL command (before splitting)
    # This catches dangerous chaining patterns like "; rm -rf /" or "&& sudo"
    matched, section = check_patterns(command, deny_patterns)
    if matched:
        return "deny", f"Blocked: '{command[:100]}' matches {section}"

    # Split into segments
    segments = split_commands(command)

    final_decision = "allow"
    final_reason = "Command matches allow patterns"

    # Track variable assignments across the chain for resolution
    env_context: dict[str, str] = {}

    for segment in segments:
        # Capture assignments BEFORE clean_segment strips them
        env_context.update(extract_assignments(segment))

        cleaned = clean_segment(segment)
        if not cleaned:
            continue

        # Substitute known variables in the cleaned command
        cleaned = substitute_known_vars(cleaned, env_context)

        # Check DENY first (per-segment)
        matched, section = check_patterns(cleaned, deny_patterns)
        if matched:
            return "deny", f"Blocked: '{cleaned}' matches {section}"

        # Check ASK
        matched, section = check_patterns(cleaned, ask_patterns)
        if matched:
            if final_decision != "ask":
                final_decision = "ask"
                final_reason = f"'{cleaned}' matches {section}"
            continue

        # Check ALLOW
        matched, _ = check_patterns(cleaned, allow_patterns)
        if matched:
            continue

        # Not in any list - mark as ask (default behavior)
        if final_decision != "ask":
            final_decision = "ask"
            final_reason = f"'{cleaned}' not in auto-approve list"

    return final_decision, final_reason


def _add_to_negative_lookahead(pattern: str, exclusion: str) -> str:
    """Prepend an exclusion alternative to the first negative lookahead in a pattern.

    Transforms: /(?!foo|bar) -> /(?!<exclusion>|foo|bar)
    If no lookahead exists, returns the pattern unchanged.
    """
    if "(?!" not in pattern:
        return pattern
    return pattern.replace("(?!", f"(?!{exclusion}|", 1)


def _inject_git_root_patterns(config: dict, git_root: str) -> None:
    """Dynamically inject git root path into validation patterns.

    When Claude runs from a git repository, rm within that repo is
    auto-approved (lockstep: excluded from ask, added to allow).
    The .git metadata directory itself is protected from deletion.

    Must be called BEFORE compile_patterns().
    """
    # Lookahead exclusion: path after the leading '/' must not start with git_root
    # E.g. /home/rrl/github/myproject -> home/rrl/github/myproject/
    escaped_for_lookahead = re.escape(git_root.lstrip("/")) + "/"

    # 1. Narrow ask.file_deletion to exclude paths within the git root.
    #    This must be paired with the allow entry below (evaluated before allow).
    ask_file_del = config.get("ask", {}).get("file_deletion", {})
    if isinstance(ask_file_del, dict) and "patterns" in ask_file_del:
        ask_file_del["patterns"] = [
            _add_to_negative_lookahead(p, escaped_for_lookahead)
            for p in ask_file_del["patterns"]
        ]

    # 2. Allow rm on any path within the git root (lockstep with exclusion above).
    escaped_abs = re.escape(git_root)
    config.setdefault("allow", {})["git_project_files"] = {
        "description": f"File removal within git repository at {git_root}",
        "patterns": [f"^rm\\s+(-[a-zA-Z]+\\s+)*{escaped_abs}/"],
    }

    # 3. Protect the .git directory itself from deletion (added to ask).
    escaped_git_dir = re.escape(os.path.join(git_root, ".git"))
    config.setdefault("ask", {})["git_metadata_protection"] = {
        "description": "Protect .git metadata directory from deletion",
        "patterns": [f"^rm\\b.*{escaped_git_dir}(/|$)"],
    }


def main():
    if len(sys.argv) != 2:
        print("Usage: validate-bash.py <config.toml>", file=sys.stderr)
        sys.exit(1)

    config_path = sys.argv[1]
    config = load_config(config_path)

    # Load and merge OS-specific patterns (e.g. bash-patterns.linux.toml)
    os_suffix = detect_os()
    config_dir = os.path.dirname(config_path)
    config_base = os.path.splitext(os.path.basename(config_path))[0]
    os_config_path = os.path.join(config_dir, f"{config_base}.{os_suffix}.toml")
    if os.path.isfile(os_config_path):
        os_config = load_config(os_config_path)
        config = merge_os_config(config, os_config)

    # Read JSON input first — cwd is needed for dynamic pattern injection
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Invalid input, let it pass
        sys.exit(0)

    command = input_data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    # If running inside a git repository, inject git-root-aware patterns before
    # compiling so that rm within the repo is auto-approved and .git is protected.
    cwd = input_data.get("cwd", "")
    if cwd:
        cwd = os.path.normpath(cwd)
        if os.path.exists(os.path.join(cwd, ".git")):
            _inject_git_root_patterns(config, cwd)

    # Compile patterns (includes any git-root injections from above)
    deny_patterns = compile_patterns(config, "deny")
    ask_patterns = compile_patterns(config, "ask")
    allow_patterns = compile_patterns(config, "allow")

    decision, reason = validate_command(
        command, deny_patterns, ask_patterns, allow_patterns
    )

    output_decision(decision, reason)


if __name__ == "__main__":
    main()
