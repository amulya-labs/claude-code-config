# Shared Python log helpers for AI Dev Foundry hook adapters.
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)
"""Python equivalents of the shell helpers in hook-lib.sh.

The output format here MUST stay byte-identical to the shell version so
existing log-grepping pipelines keep working when adapters are migrated
from shell-side logging into Python-side logging.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG_DIR = "/tmp/ai-dev-foundry-hook-logs"


def log_dir() -> str | None:
    """Return the resolved log directory, or None if unsafe to use.

    Mirrors `aidf_init_log_dir` in hook-lib.sh: respects AIDF_HOOK_LOG_DIR,
    creates the dir with mode 700, and refuses symlinks or non-owned dirs.
    """
    target = os.environ.get("AIDF_HOOK_LOG_DIR", DEFAULT_LOG_DIR)
    p = Path(target)
    try:
        p.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        return None
    try:
        st = p.lstat()
    except OSError:
        return None
    if p.is_symlink() or not p.is_dir():
        return None
    if st.st_uid != os.getuid():
        return None
    return str(p)


def sanitize_for_log(s: str) -> str:
    """Strip control characters and collapse whitespace, matching the shell helper."""
    if not s:
        return ""
    out_chars = []
    for ch in s:
        code = ord(ch)
        if ch in ("\n", "\r", "\t"):
            out_chars.append(" ")
        elif 0 <= code <= 8 or 11 <= code <= 31:
            continue
        else:
            out_chars.append(ch)
    return "".join(out_chars)


def extract_project_from_input(input_data: dict) -> str:
    """Match aidf_extract_project_from_json_input in hook-lib.sh."""
    cwd = (
        (input_data.get("cwd") or "")
        or (input_data.get("tool_input") or {}).get("directory", "")
        or (input_data.get("toolInput") or {}).get("directory", "")
    )
    if not cwd:
        return "unknown"
    if "/.claude/worktrees/" in cwd:
        before, after = cwd.split("/.claude/worktrees/", 1)
        return f"{os.path.basename(before)}-{after.split('/', 1)[0]}"
    return os.path.basename(cwd)


def log_file_for_project(directory: str, project: str) -> str:
    day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d-%a")
    return os.path.join(directory, f"{day}-{project}.log")


def write_entry(
    directory: str | None,
    project: str,
    action: str,
    command: str,
    reason: str = "",
) -> None:
    """Append a log entry in the same format as the shell helpers.

    Format (must remain byte-identical to the shell version):

        ========================================
        TIME:   YYYY-MM-DD HH:MM:SS
        ACTION: <ACTION>
        REASON: <REASON>      # only when reason is non-empty
        CMD:    <COMMAND>
        ========================================
    """
    if not directory:
        return
    path = log_file_for_project(directory, project)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    lines = [
        "========================================",
        f"TIME:   {timestamp}",
        f"ACTION: {action}",
    ]
    if reason:
        lines.append(f"REASON: {sanitize_for_log(reason)}")
    lines.append(f"CMD:    {sanitize_for_log(command)}")
    lines.append("========================================")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
