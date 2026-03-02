# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)
"""
gemini_review.py -- Phase 2 inline code review via Gemini.

Reads environment variables, optionally creates/reuses a Gemini Context Cache
for the repo codebase (Pro model only), counts tokens to guard against oversized
requests, then posts the diff to the selected model and writes a JSON array of
findings to OUTPUT_FILE.

Environment variables:
  GEMINI_API_KEY   Required. Gemini API key.
  DIFF_FOCUSED     Required. Path to the focused diff file, or raw diff content.
  SELECTED_MODEL   Default: gemini-2.0-flash. The Gemini model to use.
  REPO             Required when USE_CACHE=1. Org/repo slug (e.g. owner/repo).
  USE_CACHE        Default: 0. Set to 1 to enable context caching (Pro only).
  OUTPUT_FILE      Default: /tmp/inline-comments.json. Where to write results.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DIFF_FOCUSED_INPUT = os.environ.get("DIFF_FOCUSED", "")
SELECTED_MODEL = os.environ.get("SELECTED_MODEL", "gemini-2.0-flash")
REPO = os.environ.get("REPO", "")
USE_CACHE = os.environ.get("USE_CACHE", "0").strip() == "1"
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "/tmp/inline-comments.json")

# Maximum tokens before we bail out with an empty result
TOKEN_LIMIT = 1_000_000

# Minimum tokens needed to justify creating a context cache
CACHE_MIN_TOKENS = 32_000

# Cache TTL: 12 hours in seconds
CACHE_TTL_SECONDS = 12 * 3600

# File extensions / name patterns to skip when building the cache corpus
SKIP_PATTERNS = re.compile(
    r"package-lock\.json$|yarn\.lock$|pnpm-lock\.yaml$|Cargo\.lock$"
    r"|Gemfile\.lock$|poetry\.lock$|composer\.lock$"
    r"|\.min\.(js|css)$|\.csv$|\.tsv$|\.pb$|\.bin$"
    r"|node_modules/|\.git/|__pycache__/|\.pyc$"
)

# Binary-like extensions to skip entirely
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".whl", ".exe", ".so", ".dylib",
}


# ---------------------------------------------------------------------------
# Inline review prompt (matches the existing workflow prompt)
# ---------------------------------------------------------------------------

INLINE_PROMPT_TEMPLATE = """You are a Senior Staff Software Engineer performing a rigorous code review.
Objective: Identify bugs, security vulnerabilities, and performance bottlenecks in the provided diff.

Review Criteria:
- Logic and Correctness: Are there edge cases missed? Off-by-one errors?
- Security: Look for hardcoded secrets, injection risks, or unsafe dependencies.
- Maintainability: Is the code self-documenting? Are there better patterns?
- Actionable: Every critique MUST include a code suggestion if a fix is possible.

Constraint: Do not comment on stylistic preferences (tabs vs spaces, etc.) unless it violates a clear pattern in the existing code. Focus only on bugs, security, and significant correctness issues.

Git Diff to Review:
{diff}

Output Format: Return ONLY a valid JSON array. No markdown, no explanation, just the JSON.
Each object must follow this schema:
{{
  "file": "string (relative path to the file)",
  "line": number (the absolute line number in the new version of the file where the issue appears; use the line numbers shown after '+' in the diff hunk headers),
  "severity": "string (Critical | High | Medium | Low)",
  "comment": "string (your technical explanation of the issue)",
  "suggestion": "string (the corrected code, or empty string if no suggestion)"
}}

If you find no significant issues, return an empty JSON array: []
Cap your response at 10 items. Prioritize Critical and High severity findings.
Return ONLY the JSON array, nothing else."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def die(msg: str) -> None:
    log(f"ERROR: {msg}")
    sys.exit(1)


def load_diff() -> str:
    """Load the diff from file path or inline content."""
    if not DIFF_FOCUSED_INPUT:
        die("DIFF_FOCUSED env var is not set")

    # If it looks like a file path and the file exists, read it
    candidate = Path(DIFF_FOCUSED_INPUT)
    if candidate.exists() and candidate.is_file():
        return candidate.read_text(encoding="utf-8", errors="replace")

    # Otherwise treat it as inline content
    return DIFF_FOCUSED_INPUT


def truncate_diff(diff: str, max_chars: int = 500_000) -> str:
    """Truncate diff at max_chars, adding a notice if truncated."""
    if len(diff) <= max_chars:
        return diff
    truncated = diff[:max_chars]
    truncated += f"\n[DIFF TRUNCATED: full diff is {len(diff)} chars; only first {max_chars} shown]\n"
    return truncated


def build_cache_corpus() -> str:
    """
    Walk the current directory and collect all text source files into a single
    string to use as the context cache corpus. Skips binaries, lock files, etc.
    """
    parts = []
    cwd = Path(".")
    for path in sorted(cwd.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path)
        # Skip by name pattern
        if SKIP_PATTERNS.search(rel):
            continue
        # Skip by extension
        if path.suffix.lower() in BINARY_EXTENSIONS:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        parts.append(f"=== {rel} ===\n{content}\n")
    return "\n".join(parts)


def repo_slug(repo: str) -> str:
    """Convert 'owner/repo' to a cache display name safe slug."""
    return re.sub(r"[^a-z0-9-]", "-", repo.lower())


def write_output(data: list) -> None:
    """Write the JSON array to OUTPUT_FILE."""
    output_path = Path(OUTPUT_FILE)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log(f"Wrote {len(data)} finding(s) to {OUTPUT_FILE}")


def parse_json_response(raw_text: str) -> list:
    """
    Strip markdown code fences and parse as a JSON array.
    Returns empty list on failure.
    """
    # Strip markdown code fences
    clean = raw_text.strip()
    clean = re.sub(r"^```json\s*", "", clean)
    clean = re.sub(r"^```\s*", "", clean)
    clean = re.sub(r"\s*```$", "", clean)
    clean = clean.strip()

    try:
        parsed = json.loads(clean)
        if isinstance(parsed, list):
            return parsed
        log("WARNING: Response JSON is not an array; returning empty list")
        return []
    except json.JSONDecodeError as exc:
        log(f"WARNING: Failed to parse JSON response: {exc}")
        return []


# ---------------------------------------------------------------------------
# Context cache management (Pro model only)
# ---------------------------------------------------------------------------

def find_existing_cache(client, display_name: str):
    """
    List all caches and return the first one with matching display_name
    whose expiry is in the future. Returns None if not found.
    """
    try:
        for cache in client.caches.list():
            if getattr(cache, "display_name", None) != display_name:
                continue
            expire_time = getattr(cache, "expire_time", None)
            if expire_time is None:
                return cache
            # expire_time may be a datetime object or an ISO string
            if isinstance(expire_time, datetime):
                exp_dt = expire_time
            else:
                exp_dt = datetime.fromisoformat(str(expire_time).replace("Z", "+00:00"))
            if exp_dt > datetime.now(timezone.utc):
                log(f"Found valid existing cache: {cache.name} (expires {exp_dt})")
                return cache
        return None
    except Exception as exc:
        log(f"WARNING: Failed to list caches: {exc}")
        return None


def create_cache(client, model: str, corpus: str, display_name: str):
    """
    Create a new context cache with the repo corpus.
    Returns the cache object, or None if creation fails or corpus too small.
    """
    from google import genai
    from google.genai import types

    # Count tokens in the corpus first
    try:
        count_resp = client.models.count_tokens(model=model, contents=corpus)
        corpus_tokens = count_resp.total_tokens
        log(f"Corpus token count: {corpus_tokens}")
    except Exception as exc:
        log(f"WARNING: Token counting for corpus failed: {exc}")
        corpus_tokens = 0

    if corpus_tokens < CACHE_MIN_TOKENS:
        log(
            f"Corpus too small for caching ({corpus_tokens} tokens < {CACHE_MIN_TOKENS} minimum); "
            "skipping cache creation."
        )
        return None

    ttl_str = f"{CACHE_TTL_SECONDS}s"
    log(f"Creating context cache '{display_name}' with TTL={ttl_str}...")
    try:
        cache = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=corpus)],
                    )
                ],
                display_name=display_name,
                ttl=ttl_str,
            ),
        )
        log(f"Created cache: {cache.name}")
        return cache
    except Exception as exc:
        log(f"WARNING: Cache creation failed: {exc}; falling back to direct API call")
        return None


# ---------------------------------------------------------------------------
# Main review logic
# ---------------------------------------------------------------------------

def run_review_direct(client, model: str, prompt: str) -> list:
    """Send the prompt directly to the model (no caching)."""
    from google.genai import types

    log(f"Running inline review via {model} (direct, no cache)...")
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=4096,
            ),
        )
        raw = response.text or ""
        return parse_json_response(raw)
    except Exception as exc:
        log(f"WARNING: Gemini API call failed: {exc}")
        return []


def run_review_with_cache(client, model: str, cache_name: str, diff: str) -> list:
    """Send the prompt with a context cache reference."""
    from google.genai import types

    prompt = INLINE_PROMPT_TEMPLATE.format(diff=diff)
    log(f"Running inline review via {model} (with cache {cache_name})...")
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=4096,
                cached_content=cache_name,
            ),
        )
        raw = response.text or ""
        return parse_json_response(raw)
    except Exception as exc:
        log(f"WARNING: Gemini API call with cache failed: {exc}; retrying without cache")
        return run_review_direct(client, model, prompt)


def main() -> None:
    if not GEMINI_API_KEY:
        die("GEMINI_API_KEY is not set")

    # Load and truncate the diff
    diff_raw = load_diff()
    diff = truncate_diff(diff_raw)

    if not diff.strip():
        log("WARNING: Diff is empty; writing empty findings")
        write_output([])
        return

    # Build the full prompt for token counting and direct calls
    prompt = INLINE_PROMPT_TEMPLATE.format(diff=diff)

    # Import the SDK
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        die("google-generativeai is not installed. Run: pip install google-generativeai")

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Count tokens before sending
    log(f"Counting tokens for model '{SELECTED_MODEL}'...")
    try:
        count_resp = client.models.count_tokens(model=SELECTED_MODEL, contents=prompt)
        total_tokens = count_resp.total_tokens
        log(f"Estimated token count: {total_tokens}")
    except Exception as exc:
        log(f"WARNING: Token counting failed: {exc}; proceeding without count")
        total_tokens = 0

    if total_tokens > TOKEN_LIMIT:
        log(
            f"WARNING: Token count ({total_tokens}) exceeds limit ({TOKEN_LIMIT}); "
            "skipping inline review to avoid quota exhaustion."
        )
        write_output([])
        return

    # Determine whether to use caching
    is_pro_model = "pro" in SELECTED_MODEL.lower() or "1.5" in SELECTED_MODEL

    if USE_CACHE and is_pro_model and REPO:
        display_name = f"cache-{repo_slug(REPO)}"

        # Try to reuse an existing valid cache
        existing = find_existing_cache(client, display_name)

        if existing:
            findings = run_review_with_cache(client, SELECTED_MODEL, existing.name, diff)
        else:
            # Build corpus and try to create a new cache
            log("Building repo corpus for context cache...")
            corpus = build_cache_corpus()
            cache = create_cache(client, SELECTED_MODEL, corpus, display_name)

            if cache:
                findings = run_review_with_cache(client, SELECTED_MODEL, cache.name, diff)
            else:
                # Cache unavailable — fall back to direct call
                findings = run_review_direct(client, SELECTED_MODEL, prompt)
    else:
        # Flash model or caching disabled: direct call
        findings = run_review_direct(client, SELECTED_MODEL, prompt)

    write_output(findings)


if __name__ == "__main__":
    main()
