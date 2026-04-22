#!/usr/bin/env python3
"""
Bash command validator with a provider-neutral JSON contract.
Reads patterns from TOML config and validates commands.

Source: https://github.com/amulya-labs/ai-dev-foundry
License: MIT (https://opensource.org/licenses/MIT)
"""

import json
import os
import re
import sys
from dataclasses import dataclass

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
    """Detect OS platform and return the suffix for OS-specific config files."""
    platform = sys.platform
    if platform.startswith("linux"):
        return "linux"
    elif platform == "darwin":
        return "darwin"
    elif platform in ("win32", "cygwin", "msys"):
        return "windows"
    return platform


def merge_os_config(base: dict, overlay: dict) -> dict:
    """Merge OS-specific config into base config with additive-append semantics."""
    for category in ("deny", "ask", "allow"):
        overlay_sections = overlay.get(category, {})
        if not overlay_sections:
            continue
        base.setdefault(category, {})
        for section_name, section_data in overlay_sections.items():
            if not isinstance(section_data, dict) or "patterns" not in section_data:
                continue
            if section_name in base[category] and isinstance(base[category][section_name], dict):
                base[category][section_name]["patterns"] = (
                    base[category][section_name].get("patterns", []) + section_data["patterns"]
                )
            else:
                base[category][section_name] = section_data
    return base


def compile_patterns(config: dict, category: str) -> list[CompiledPattern]:
    """Extract and compile patterns for a category (deny/ask/allow)."""
    compiled = []
    for section_name, section in config.get(category, {}).items():
        if isinstance(section, dict) and "patterns" in section:
            for pattern in section["patterns"]:
                try:
                    regex = re.compile(pattern)
                    compiled.append(
                        CompiledPattern(regex=regex, section=f"{category}.{section_name}", original=pattern)
                    )
                except re.error as e:
                    print(
                        f"Warning: Invalid regex '{pattern}' in {category}.{section_name}: {e}",
                        file=sys.stderr,
                    )
    return compiled


def strip_env_vars(cmd: str) -> str:
    """Strip environment variable assignments from command start."""
    while True:
        cmd = cmd.lstrip()
        match = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", cmd)
        if not match:
            break

        rest = cmd[match.end() :]

        if rest.startswith("$("):
            depth = 1
            i = 2
            while depth > 0 and i < len(rest):
                if rest[i] == "(":
                    depth += 1
                elif rest[i] == ")":
                    depth -= 1
                i += 1
            cmd = rest[i:]
        elif rest.startswith("`"):
            end = rest.find("`", 1)
            cmd = rest[end + 1 :] if end > 0 else ""
        elif rest.startswith('"'):
            i = 1
            while i < len(rest):
                if rest[i] == "\\" and i + 1 < len(rest):
                    i += 2
                    continue
                if rest[i] == '"':
                    break
                i += 1
            cmd = rest[i + 1 :]
        elif rest.startswith("'"):
            end = rest.find("'", 1)
            cmd = rest[end + 1 :] if end > 0 else ""
        elif rest.startswith("$") and len(rest) > 1 and re.match(r"[A-Za-z_]", rest[1]):
            var_match = re.match(r"^\$[A-Za-z_][A-Za-z0-9_]*", rest)
            cmd = rest[var_match.end() :] if var_match else rest
        else:
            val_match = re.match(r"^[^\s]*\s*", rest)
            cmd = rest[val_match.end() :] if val_match else ""

    return cmd.lstrip()


def strip_leading_comment(cmd: str) -> str:
    """Strip shell comments from the start of a command."""
    lines = cmd.split("\n")
    while lines and lines[0].strip().startswith("#"):
        lines.pop(0)
    return "\n".join(lines).lstrip()


def _find_matching_paren(cmd: str, start: int) -> int:
    """Find the matching ')' for a '(' that was just consumed."""
    depth = 1
    i = start
    inner_quote = None
    heredoc_delim = None

    while i < len(cmd) and depth > 0:
        char = cmd[i]

        if heredoc_delim is not None:
            if char == "\n":
                next_nl = cmd.find("\n", i + 1)
                if next_nl == -1:
                    next_nl = len(cmd)
                raw_line = cmd[i + 1 : next_nl]
                if raw_line == heredoc_delim:
                    i = next_nl
                    heredoc_delim = None
                    continue
            i += 1
            continue

        if char == "\\" and i + 1 < len(cmd) and inner_quote != "'":
            i += 2
            continue

        if char in ('"', "'"):
            if inner_quote is None:
                inner_quote = char
            elif inner_quote == char:
                inner_quote = None
        elif inner_quote is None:
            if char == "<" and cmd[i : i + 2] == "<<" and (i + 2 >= len(cmd) or cmd[i + 2] != "<"):
                delim, _strip, end_pos = _parse_heredoc_delim(cmd, i + 2)
                if delim:
                    i = end_pos
                    heredoc_delim = delim
                    continue
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1

        i += 1

    return i


def _parse_heredoc_delim(cmd: str, pos: int) -> tuple:
    """Parse a heredoc delimiter starting after '<<'."""
    j = pos
    strip_tabs = False
    if j < len(cmd) and cmd[j] == "-":
        strip_tabs = True
        j += 1
    while j < len(cmd) and cmd[j] in " \t":
        j += 1
    if j >= len(cmd) or cmd[j] == "\n":
        return None, False, pos

    if cmd[j] in ("'", '"'):
        delim_quote = cmd[j]
        k = cmd.find(delim_quote, j + 1)
        if k > j + 1:
            return cmd[j + 1 : k], strip_tabs, k + 1
        return None, False, pos
    else:
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", cmd[j:])
        if m:
            return m.group(0), strip_tabs, j + m.end()
        return None, False, pos


def split_commands(cmd: str) -> list[str]:
    """Split command on &&, ||, ;, newlines while respecting shell syntax."""
    segments = []
    current = ""
    quote = None
    i = 0
    heredoc_delim = None
    heredoc_strip_tabs = False

    while i < len(cmd):
        char = cmd[i]

        if heredoc_delim is not None:
            current += char
            if char == "\n":
                next_nl = cmd.find("\n", i + 1)
                if next_nl == -1:
                    next_nl = len(cmd)
                raw_line = cmd[i + 1 : next_nl]
                candidate = raw_line.lstrip("\t") if heredoc_strip_tabs else raw_line
                if candidate == heredoc_delim:
                    current += raw_line
                    i = next_nl
                    heredoc_delim = None
                    heredoc_strip_tabs = False
                    continue
            i += 1
            continue

        if char in ('"', "'"):
            backslash_count = 0
            j = i - 1
            while j >= 0 and cmd[j] == "\\":
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:
                if quote is None:
                    quote = char
                elif quote == char:
                    quote = None

        if char == "$" and i + 1 < len(cmd) and cmd[i + 1] == "(" and quote != "'":
            end = _find_matching_paren(cmd, i + 2)
            current += cmd[i:end]
            i = end
            continue

        if quote is None:
            if char == "<" and cmd[i : i + 2] == "<<" and (i + 2 >= len(cmd) or cmd[i + 2] != "<"):
                delim, strip_tabs, end_pos = _parse_heredoc_delim(cmd, i + 2)
                if delim:
                    current += cmd[i:end_pos]
                    i = end_pos
                    heredoc_delim = delim
                    heredoc_strip_tabs = strip_tabs
                    continue

            if cmd[i : i + 2] in ("&&", "||"):
                if current.strip():
                    segments.append(current)
                current = ""
                i += 2
                continue
            elif char == ";":
                if cmd[i : i + 2] == ";;":
                    current += ";;"
                    i += 2
                    continue
                if current.endswith("\\"):
                    current += char
                    i += 1
                    continue
                if current.strip():
                    segments.append(current)
                current = ""
                i += 1
                continue
            elif char == "\n":
                if current.endswith("\\"):
                    current = current[:-1]
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


CONTROL_FLOW_KEYWORDS = re.compile(r"^(then|else|elif|do)\s+", re.IGNORECASE)
CONTROL_FLOW_TERMINATORS = re.compile(r"^(done|fi|esac)(\s+[\d<>|&].*|\s*$)", re.IGNORECASE)


def extract_assignments(segment: str) -> dict[str, str]:
    """Extract VAR=literal assignments from a raw segment."""
    env = {}
    rest = segment.lstrip()
    while True:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", rest)
        if not match:
            break

        name = match.group(1)
        rest = rest[match.end() :]

        if rest.startswith("$(") or rest.startswith("`"):
            break
        elif rest.startswith("${"):
            break
        elif rest.startswith("$") and len(rest) > 1 and re.match(r"[A-Za-z_]", rest[1]):
            break
        elif rest.startswith('"'):
            i = 1
            while i < len(rest):
                if rest[i] == "\\" and i + 1 < len(rest):
                    i += 2
                    continue
                if rest[i] == '"':
                    break
                i += 1
            else:
                break
            captured = rest[1:i]
            if "$(" in captured or "`" in captured or "${" in captured:
                break
            env[name] = captured
            rest = rest[i + 1 :]
        elif rest.startswith("'"):
            end = rest.find("'", 1)
            if end > 0:
                env[name] = rest[1:end]
                rest = rest[end + 1 :]
            else:
                break
        else:
            val_match = re.match(r"^([^\s]*)", rest)
            env[name] = val_match.group(1) if val_match else ""
            rest = rest[val_match.end() :] if val_match else ""

        rest = rest.lstrip()

    return env


def substitute_known_vars(segment: str, env: dict[str, str]) -> str:
    """Replace $VAR or ${VAR} at position 0 of a cleaned segment with known values."""
    if not segment.startswith("$"):
        return segment

    match = re.match(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}", segment)
    if match:
        name = match.group(1)
        if name in env:
            return env[name] + segment[match.end() :]
        return segment

    match = re.match(r"^\$([A-Za-z_][A-Za-z0-9_]*)", segment)
    if match:
        name = match.group(1)
        if name in env:
            return env[name] + segment[match.end() :]

    return segment


_SHELL_META = re.compile(r"&&|\|\||;|\||\$\(|`|\(|>>?|>&")


def strip_bash_c_wrapper(segment: str) -> str:
    """Unwrap simple bash -c / sh -c wrappers to expose the inner command."""
    match = re.match(r"^(?:/bin/)?(bash|sh)\s+-c\s+([\'\"])(.*)\2\s*$", segment, re.DOTALL)
    if not match:
        return segment

    delimiter = match.group(2)
    inner = match.group(3)

    if "\n" in inner or "\r" in inner:
        return segment
    if delimiter in inner:
        return segment
    if _SHELL_META.search(inner):
        return segment

    return inner


def strip_control_flow_keyword(segment: str) -> str:
    """Strip shell control flow keywords from segment start."""
    if CONTROL_FLOW_TERMINATORS.match(segment):
        return ""

    match = CONTROL_FLOW_KEYWORDS.match(segment)
    if match:
        return segment[match.end() :].lstrip()

    return segment


def strip_line_continuations(cmd: str) -> str:
    r"""Strip shell line continuations (\<newline>) from command."""
    return cmd.replace("\\\n", " ")


def _is_bare_redirect(segment: str) -> bool:
    """Check if a segment is just a bare redirect/pipe tail with no real command."""
    s = re.sub(r"\d*>>?&?\d*\s*/?[\w./-]*", "", segment)
    s = re.sub(
        r"\|\s*(head|tail|sort|wc|uniq|tee|grep|sed|awk|cut|tr|column|less|more|cat|fmt|nl|rev|paste|comm|fold|expand|unexpand|pr)\b[^|]*",
        "",
        s,
    )
    s = re.sub(r"\|", "", s)
    return not s.strip()


def _strip_case_label(segment: str) -> str:
    """Strip case-statement pattern labels from segment start."""
    match = re.match(r"^(\S+)\)\s+", segment)
    if match and "$(" not in match.group(1) and "(" not in match.group(1):
        return segment[match.end() :]
    return segment


def clean_segment(segment: str) -> str:
    """Clean a command segment: strip whitespace, subshell chars, env vars, comments."""
    segment = segment.strip()

    while segment.startswith("\\") and (len(segment) == 1 or segment[1] in " \t\n"):
        segment = segment[1:].lstrip()

    segment = strip_leading_comment(segment)
    segment = strip_env_vars(segment)
    if not segment.strip():
        return ""

    while segment and segment[0] in "({":
        segment = segment[1:].lstrip()
    while segment and segment[-1] in ")}":
        segment = segment[:-1].rstrip()

    if _is_bare_redirect(segment):
        return ""

    segment = strip_bash_c_wrapper(segment)
    segment = strip_control_flow_keyword(segment)
    segment = _strip_case_label(segment)
    segment = strip_env_vars(segment)
    if not segment.strip():
        return ""

    return segment


def check_patterns(segment: str, patterns: list[CompiledPattern]) -> tuple[bool, str]:
    """Check if segment matches any compiled pattern."""
    for pattern in patterns:
        if pattern.regex.search(segment):
            return True, pattern.section
    return False, ""


def validate_command(
    command: str,
    deny_patterns: list[CompiledPattern],
    ask_patterns: list[CompiledPattern],
    allow_patterns: list[CompiledPattern],
) -> tuple[str, str]:
    """Validate a command against patterns."""
    command = strip_line_continuations(command)

    matched, section = check_patterns(command, deny_patterns)
    if matched:
        return "deny", f"Blocked: '{command[:100]}' matches {section}"

    segments = split_commands(command)
    final_decision = "allow"
    final_reason = "Command matches allow patterns"
    env_context: dict[str, str] = {}

    for segment in segments:
        env_context.update(extract_assignments(segment))

        cleaned = clean_segment(segment)
        if not cleaned:
            continue

        cleaned = substitute_known_vars(cleaned, env_context)

        matched, section = check_patterns(cleaned, deny_patterns)
        if matched:
            return "deny", f"Blocked: '{cleaned}' matches {section}"

        matched, section = check_patterns(cleaned, ask_patterns)
        if matched:
            if final_decision != "ask":
                final_decision = "ask"
                final_reason = f"'{cleaned}' matches {section}"
            continue

        matched, _ = check_patterns(cleaned, allow_patterns)
        if matched:
            continue

        if final_decision != "ask":
            final_decision = "ask"
            final_reason = f"'{cleaned}' not in auto-approve list"

    return final_decision, final_reason


def _add_to_negative_lookahead(pattern: str, exclusion: str) -> str:
    """Prepend an exclusion alternative to the first negative lookahead in a pattern."""
    if "(?!" not in pattern:
        return pattern
    return pattern.replace("(?!", f"(?!{exclusion}|", 1)


def _inject_git_root_patterns(config: dict, git_root: str) -> None:
    """Dynamically inject git root path into validation patterns."""
    escaped_for_lookahead = re.escape(git_root.lstrip("/")) + "/"

    ask_file_del = config.get("ask", {}).get("file_deletion", {})
    if isinstance(ask_file_del, dict) and "patterns" in ask_file_del:
        ask_file_del["patterns"] = [
            _add_to_negative_lookahead(p, escaped_for_lookahead) for p in ask_file_del["patterns"]
        ]

    escaped_abs = re.escape(git_root)
    config.setdefault("allow", {})["git_project_files"] = {
        "description": f"File removal within git repository at {git_root}",
        "patterns": [f"^rm\\s+(-[a-zA-Z]+\\s+)*{escaped_abs}/"],
    }

    escaped_git_dir = re.escape(os.path.join(git_root, ".git"))
    config.setdefault("ask", {})["git_metadata_protection"] = {
        "description": "Protect .git metadata directory from deletion",
        "patterns": [f"^rm\\b.*{escaped_git_dir}(/|$)"],
    }


def load_runtime_config(config_path: str, cwd: str = "") -> dict:
    """Load base config, merge OS overlay, and inject cwd-aware patterns."""
    config = load_config(config_path)

    os_suffix = detect_os()
    config_dir = os.path.dirname(config_path)
    config_base = os.path.splitext(os.path.basename(config_path))[0]
    os_config_path = os.path.join(config_dir, f"{config_base}.{os_suffix}.toml")
    if os.path.isfile(os_config_path):
        os_config = load_config(os_config_path)
        config = merge_os_config(config, os_config)

    if cwd:
        cwd = os.path.normpath(cwd)
        if os.path.exists(os.path.join(cwd, ".git")):
            _inject_git_root_patterns(config, cwd)

    return config


def evaluate_request(request: dict, config_path: str) -> tuple[str, str]:
    """Evaluate a normalized request with fields like command and cwd."""
    command = request.get("command", "")
    if not command:
        return "allow", "No command to validate"

    config = load_runtime_config(config_path, request.get("cwd", ""))
    deny_patterns = compile_patterns(config, "deny")
    ask_patterns = compile_patterns(config, "ask")
    allow_patterns = compile_patterns(config, "allow")
    return validate_command(command, deny_patterns, ask_patterns, allow_patterns)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: validate-command.py <config.toml>", file=sys.stderr)
        sys.exit(1)

    try:
        request = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    command = request.get("command", "")
    if not command:
        sys.exit(0)

    decision, reason = evaluate_request(request, sys.argv[1])
    print(json.dumps({"decision": decision, "reason": reason}))


if __name__ == "__main__":
    main()
