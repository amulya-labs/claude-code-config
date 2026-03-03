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

# Retry configuration for transient API errors (429, 5xx)
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 2  # doubles each attempt: 2s, 4s, 8s

# HTTP status codes that warrant a retry
RETRYABLE_EXCEPTION_SUBSTRINGS = ("429", "500", "502", "503", "504", "quota", "rate")

# File extensions / name patterns to skip when building the cache corpus.
# Covers lock files, build artifacts, compiled assets, AND common secret/credential files.
SKIP_PATTERNS = re.compile(
    # Lock files
    r"package-lock\.json$|yarn\.lock$|pnpm-lock\.yaml$|Cargo\.lock$"
    r"|Gemfile\.lock$|poetry\.lock$|composer\.lock$"
    # Minified / data files
    r"|\.min\.(js|css)$|\.csv$|\.tsv$|\.pb$|\.bin$"
    # Tooling dirs
    r"|node_modules/|\.git/|__pycache__/|\.pyc$"
    # Secret / credential files
    r"|\.env$|\.env\."
    r"|\.pem$|\.key$|\.p12$|\.pfx$|\.crt$|\.cer$"
    r"|\.tfvars$|\.tfstate$|\.tfstate\.backup$"
    r"|credential|secret|\.vault$"
    r"|id_rsa|id_ed25519|id_ecdsa|id_dsa"
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
    """
    Truncate diff at max_chars, adding a notice if truncated.

    Note: the 500k char limit is intentionally conservative relative to the
    1M-token TOKEN_LIMIT. At roughly 3-4 chars per token, 500k chars maps to
    approximately 125k-167k tokens for the diff alone, leaving ample headroom
    for the prompt template and any cached corpus content.
    """
    if len(diff) <= max_chars:
        return diff
    truncated = diff[:max_chars]
    truncated += f"\n[DIFF TRUNCATED: full diff is {len(diff)} chars; only first {max_chars} shown]\n"
    return truncated


def build_cache_corpus() -> str:
    """
    Walk the current directory and collect all text source files into a single
    string to use as the context cache corpus. Skips binaries, lock files,
    and secret/credential files to avoid uploading sensitive data to the API.
    """
    parts = []
    cwd = Path(".")
    for path in sorted(cwd.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path)
        # Skip by name pattern (lock files, build artifacts, secrets)
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
    clean = raw_text.strip()
    # Strip an opening ```json or ``` fence (only one of these can match)
    clean = re.sub(r"^```(?:json)?\s*", "", clean)
    # Strip a closing ``` fence
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


def _is_retryable_error(exc: Exception) -> bool:
    """Return True if the exception looks like a transient quota or server error."""
    msg = str(exc).lower()
    return any(substr in msg for substr in RETRYABLE_EXCEPTION_SUBSTRINGS)


def _call_with_retry(fn, description: str):
    """
    Call fn() with exponential-backoff retry on transient errors (429, 5xx).
    Raises the last exception if all attempts are exhausted.
    """
    delay = RETRY_BASE_DELAY_SECONDS
    last_exc = None
    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < RETRY_MAX_ATTEMPTS and _is_retryable_error(exc):
                log(
                    f"WARNING: {description} attempt {attempt}/{RETRY_MAX_ATTEMPTS} "
                    f"failed with transient error: {exc}; retrying in {delay}s..."
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise last_exc  # unreachable but satisfies type checkers


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
            # expire_time may be a datetime object (aware or naive) or an ISO string
            if isinstance(expire_time, datetime):
                exp_dt = expire_time
            else:
                exp_dt = datetime.fromisoformat(str(expire_time).replace("Z", "+00:00"))
            # Ensure exp_dt is timezone-aware before comparing with UTC now
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
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
    """
    Send the prompt directly to the model (no caching).
    Retries on transient 429/5xx errors with exponential backoff.
    """
    from google.genai import types

    log(f"Running inline review via {model} (direct, no cache)...")

    def _call():
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=4096,
            ),
        )

    try:
        response = _call_with_retry(_call, f"generate_content ({model})")
        raw = response.text or ""
        return parse_json_response(raw)
    except Exception as exc:
        log(f"WARNING: Gemini API call failed after all retries: {exc}")
        return []


def run_review_with_cache(client, model: str, cache_name: str, diff: str) -> list:
    """
    Send the prompt with a context cache reference.
    Retries on transient errors; falls back to direct call on cache errors.
    """
    from google.genai import types

    prompt = INLINE_PROMPT_TEMPLATE.format(diff=diff)
    log(f"Running inline review via {model} (with cache {cache_name})...")

    def _call():
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=4096,
                cached_content=cache_name,
            ),
        )

    try:
        response = _call_with_retry(_call, f"generate_content with cache ({model})")
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
    except ImportError:
        die("google-genai is not installed. Run: pip install google-genai")

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

    # Determine whether to use caching.
    # Only "pro" tier models support context caching; do NOT match on version
    # strings like "1.5" which would incorrectly classify gemini-1.5-flash as Pro.
    is_pro_model = "pro" in SELECTED_MODEL.lower()

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
